from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

from pydantic import ValidationError

from morning_radio.artifacts.io import atomic_write_json
from morning_radio.llm.client import LLMClient, LLMError, allows_fixture_fallback
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
        source_aliases = {
            f"source-{index}": source.candidate_id
            for index, source in enumerate(sources[:2], start=1)
        }
        if llm is not None:
            try:
                response = llm.generate_structured(
                    DOSSIER_SYSTEM,
                    json.dumps(
                        {
                            "cluster": {
                                "cluster_id": cluster.cluster_id,
                                "canonical_title": cluster.canonical_title,
                                "source_count": cluster.source_count,
                                "topic_hints": cluster.topic_hints,
                            },
                            "selected_story": {
                                "estimated_seconds": story.estimated_seconds,
                            },
                            "sources": [
                                {
                                    "candidate_id": f"source-{index}",
                                    "title": source.title,
                                    "text_excerpt": source.text[:6000],
                                }
                                for index, source in enumerate(sources[:2], start=1)
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    DossierResponse,
                    stage="researching",
                    prompt_type="story_dossier",
                )
                model_dossier = _restore_source_ids(response.dossier, source_aliases)
                if _valid_source_ids(
                    model_dossier,
                    {source.candidate_id for source in sources},
                ) and _is_substantive(model_dossier):
                    dossier = model_dossier
                    atomic_write_json(
                        dossier_dir / f"{cluster.cluster_id}.json",
                        dossier.model_dump(mode="json"),
                    )
                    if dossier.safe_for_scripting:
                        duplicate = _find_duplicate_dossier(dossier, dossiers)
                        if duplicate is None:
                            dossiers.append(dossier)
                        else:
                            rejections.append(
                                {
                                    "cluster_id": dossier.cluster_id,
                                    "reason": f"duplicate_of:{duplicate.cluster_id}",
                                }
                            )
                    else:
                        rejections.append(
                            {
                                "cluster_id": dossier.cluster_id,
                                "reason": "llm_marked_unsafe",
                            }
                        )
                    continue
                atomic_write_json(
                    dossier_dir / f"{cluster.cluster_id}-fallback.json",
                    {
                        "model": llm.model,
                        "reason": "Model dossier was insufficiently grounded or substantive.",
                        "fixture_fallback": allows_fixture_fallback(llm),
                    },
                )
            except (LLMError, ValidationError) as exc:
                atomic_write_json(
                    dossier_dir / f"{cluster.cluster_id}-fallback.json",
                    {
                        "model": llm.model,
                        "reason": str(exc),
                        "fixture_fallback": allows_fixture_fallback(llm),
                    },
                )
        facts = _fallback_facts(cluster, sources)
        safe = bool(facts)
        fact_claims = [fact.claim for fact in facts]
        fallback_source_ids = sorted(
            {
                candidate_id
                for fact in facts
                for candidate_id in fact.supporting_candidate_ids
            }
        )
        dossier = StoryDossier(
            cluster_id=cluster.cluster_id,
            working_headline=cluster.canonical_title,
            what_happened=" ".join(fact_claims[:2]) if facts else "Insufficient source text.",
            what_is_new_today=fact_claims[2] if len(fact_claims) > 2 else "",
            why_it_matters=fact_claims[3] if len(fact_claims) > 3 else "",
            background_needed=fact_claims[4] if len(fact_claims) > 4 else "",
            facts=facts,
            uncertainties=[] if len(sources) > 1 else ["Single-source story; phrase cautiously."],
            source_disagreements=[],
            do_not_claim=["Do not add facts that are absent from the source excerpts."],
            recommended_seconds=story.estimated_seconds,
            source_ids=fallback_source_ids,
            safe_for_scripting=safe,
        )
        atomic_write_json(dossier_dir / f"{cluster.cluster_id}.json", dossier.model_dump(mode="json"))
        if safe:
            duplicate = _find_duplicate_dossier(dossier, dossiers)
            if duplicate is None:
                dossiers.append(dossier)
            else:
                rejections.append(
                    {
                        "cluster_id": dossier.cluster_id,
                        "reason": f"duplicate_of:{duplicate.cluster_id}",
                    }
                )
        else:
            rejections.append({"cluster_id": cluster.cluster_id, "reason": "no_usable_source_facts"})
    if not dossiers:
        raise RuntimeError("No selected story had enough source support for a dossier.")
    atomic_write_json(
        dossier_dir / "backfill-history.json",
        {
            "target_count": target_count,
            "attempted_cluster_ids": sorted(attempted),
            "final_cluster_ids": [item.cluster_id for item in dossiers],
            "rejections": rejections,
        },
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


def _restore_source_ids(
    dossier: StoryDossier,
    aliases: dict[str, str],
) -> StoryDossier:
    def restore(source_id: str) -> str:
        return aliases.get(source_id, source_id)

    facts = [
        fact.model_copy(
            update={
                "supporting_candidate_ids": [
                    restore(source_id) for source_id in fact.supporting_candidate_ids
                ]
            }
        )
        for fact in dossier.facts
    ]
    return dossier.model_copy(
        update={
            "facts": facts,
            "source_ids": [restore(source_id) for source_id in dossier.source_ids],
        }
    )


def _is_substantive(dossier: StoryDossier) -> bool:
    if not dossier.safe_for_scripting:
        return True
    fact_words = sum(len(fact.claim.split()) for fact in dossier.facts)
    narrative_word_counts = [
        len(dossier.what_happened.split()),
        len(dossier.what_is_new_today.split()),
        len(dossier.why_it_matters.split()),
        len(dossier.background_needed.split()),
    ]
    return (
        3 <= len(dossier.facts) <= 8
        and 35 <= fact_words <= 220
        and 20 <= narrative_word_counts[0] <= 120
        and all(word_count <= 80 for word_count in narrative_word_counts[1:])
    )


def _fallback_facts(
    cluster: Cluster,
    sources: list[ExtractionResult],
    maximum_facts: int = 7,
) -> list[DossierFact]:
    facts: list[DossierFact] = []
    seen: set[str] = set()
    for source in sources[:3]:
        for sentence in _useful_sentences(source.text, cluster.canonical_title):
            key = re.sub(r"[^a-z0-9]+", " ", sentence.casefold()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            facts.append(
                DossierFact(
                    claim=sentence,
                    supporting_candidate_ids=[source.candidate_id],
                )
            )
            if len(facts) >= maximum_facts:
                return facts
    return facts


def _useful_sentences(text: str, title: str) -> list[str]:
    cleaned = " ".join(unescape(text).split())
    cleaned = re.sub(
        r"^" + re.escape(title) + r"\s*(?:-\s*Published)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c\u2018])", cleaned)
    boilerplate = (
        "call the ",
        "get in touch",
        "in case you missed",
        "make sure you subscribe",
        "today on ",
        "watch | listen",
        "we want to hear",
        "you're reading a newsletter",
    )
    useful: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        words = sentence.split()
        lowered = sentence.casefold()
        if not 3 <= len(words) <= 70:
            continue
        if any(marker in lowered for marker in boilerplate):
            continue
        useful.append(sentence)
    return useful


def _find_duplicate_dossier(
    candidate: StoryDossier,
    existing: list[StoryDossier],
) -> StoryDossier | None:
    candidate_tokens = _dossier_event_tokens(candidate)
    for dossier in existing:
        existing_tokens = _dossier_event_tokens(dossier)
        shared = candidate_tokens & existing_tokens
        smaller = min(len(candidate_tokens), len(existing_tokens))
        if len(shared) >= 8 and smaller and len(shared) / smaller >= 0.22:
            return dossier
    return None


def _dossier_event_tokens(dossier: StoryDossier) -> set[str]:
    text = " ".join(
        [
            dossier.working_headline,
            dossier.what_happened,
            dossier.what_is_new_today,
            *(fact.claim for fact in dossier.facts),
        ]
    )
    stopwords = {
        "about",
        "after",
        "company",
        "from",
        "have",
        "into",
        "that",
        "their",
        "they",
        "this",
        "were",
        "with",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.casefold())
        if len(word) > 3 and word not in stopwords
    }
