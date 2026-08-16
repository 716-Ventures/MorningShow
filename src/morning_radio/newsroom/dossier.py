from __future__ import annotations

import json
from pathlib import Path

from morning_radio.llm.client import LLMClient
from morning_radio.llm.prompts import DOSSIER_SYSTEM
from morning_radio.llm.schemas import DossierResponse
from morning_radio.models import (
    Cluster,
    DossierFact,
    ExtractionResult,
    SelectedStory,
    SelectionResult,
    StoryDossier,
)


def build_dossiers(
    selected: SelectionResult | list[SelectedStory],
    clusters: list[Cluster],
    extractions: list[ExtractionResult],
    run_dir: Path,
    llm: LLMClient | None = None,
) -> list[StoryDossier]:
    cluster_by_id = {item.cluster_id: item for item in clusters}
    extraction_by_id = {item.candidate_id: item for item in extractions}
    dossiers: list[StoryDossier] = []
    rejections: list[dict[str, str]] = []
    dossier_dir = run_dir / "dossiers"
    dossier_dir.mkdir(exist_ok=True)
    primary_stories, backfill_stories = selection_queues(selected)
    queue = [*primary_stories, *backfill_stories]
    attempted: set[str] = set()
    target_count = len(primary_stories)
    for story in queue:
        if len(dossiers) >= target_count:
            break
        if story.cluster_id in attempted:
            continue
        attempted.add(story.cluster_id)
        cluster = cluster_by_id[story.cluster_id]
        sources = [
            extraction_by_id[candidate_id]
            for candidate_id in cluster.candidate_ids
            if candidate_id in extraction_by_id
            and extraction_by_id[candidate_id].extraction_status == "usable"
        ]
        if llm is not None:
            try:
                response = llm.generate_structured(
                    DOSSIER_SYSTEM,
                    json.dumps(
                        {
                            "cluster": cluster.model_dump(mode="json"),
                            "selected_story": story.model_dump(mode="json"),
                            "sources": [
                                {
                                    "candidate_id": source.candidate_id,
                                    "url": source.url,
                                    "title": source.title,
                                    "text_excerpt": source.text[:5000],
                                }
                                for source in sources
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    DossierResponse,
                    stage="researching",
                    prompt_type="story_dossier",
                )
                if _valid_source_ids(response.dossier, {source.candidate_id for source in sources}):
                    dossier = response.dossier
                    (dossier_dir / f"{cluster.cluster_id}.json").write_text(
                        json.dumps(dossier.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    if dossier.safe_for_scripting:
                        dossiers.append(dossier)
                    else:
                        rejections.append(
                            {
                                "cluster_id": dossier.cluster_id,
                                "reason": "llm_marked_unsafe",
                            }
                        )
                    continue
            except Exception:
                if llm.model != "fake-local-fixture":
                    raise
        facts = [
            DossierFact(
                claim=_first_sentence(source.text) or cluster.canonical_title,
                supporting_candidate_ids=[source.candidate_id],
            )
            for source in sources[:3]
        ]
        safe = bool(facts)
        dossier = StoryDossier(
            cluster_id=cluster.cluster_id,
            working_headline=cluster.canonical_title,
            what_happened=facts[0].claim if facts else "Insufficient source text.",
            what_is_new_today="This item appeared in the current discovery window.",
            why_it_matters="It scored highly against the configured editorial profile.",
            background_needed="Use only the cited source facts and avoid unsupported context.",
            facts=facts,
            uncertainties=[] if len(sources) > 1 else ["Single-source story; phrase cautiously."],
            source_disagreements=[],
            do_not_claim=["Do not add facts that are absent from the source excerpts."],
            recommended_seconds=story.estimated_seconds,
            source_ids=[source.candidate_id for source in sources],
            safe_for_scripting=safe,
        )
        (dossier_dir / f"{cluster.cluster_id}.json").write_text(
            json.dumps(dossier.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if safe:
            dossiers.append(dossier)
        else:
            rejections.append({"cluster_id": cluster.cluster_id, "reason": "no_usable_source_facts"})
    if not dossiers:
        raise RuntimeError("No selected story had enough source support for a dossier.")
    (dossier_dir / "backfill-history.json").write_text(
        json.dumps(
            {
                "target_count": target_count,
                "attempted_cluster_ids": sorted(attempted),
                "final_cluster_ids": [item.cluster_id for item in dossiers],
                "rejections": rejections,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return dossiers


def selection_queues(selected: SelectionResult | list[SelectedStory]) -> tuple[list[SelectedStory], list[SelectedStory]]:
    if isinstance(selected, SelectionResult):
        return selected.selected, selected.not_selected_high_score
    return selected, []


def _valid_source_ids(dossier: StoryDossier, known_ids: set[str]) -> bool:
    if not set(dossier.source_ids) <= known_ids:
        return False
    if not all(set(fact.supporting_candidate_ids) <= known_ids for fact in dossier.facts):
        return False
    if dossier.safe_for_scripting:
        if not dossier.facts or not dossier.source_ids:
            return False
        fact_ids = {
            candidate_id
            for fact in dossier.facts
            for candidate_id in fact.supporting_candidate_ids
        }
        return fact_ids == set(dossier.source_ids)
    return True


def _first_sentence(text: str) -> str:
    for separator in (". ", "? ", "! "):
        if separator in text:
            return text.split(separator, 1)[0].strip() + separator.strip()
    return text[:240].strip()
