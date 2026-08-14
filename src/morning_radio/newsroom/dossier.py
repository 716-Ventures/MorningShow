from __future__ import annotations

import json
from pathlib import Path

from morning_radio.models import Cluster, DossierFact, ExtractionResult, SelectedStory, StoryDossier


def build_dossiers(
    selected: list[SelectedStory],
    clusters: list[Cluster],
    extractions: list[ExtractionResult],
    run_dir: Path,
) -> list[StoryDossier]:
    cluster_by_id = {item.cluster_id: item for item in clusters}
    extraction_by_id = {item.candidate_id: item for item in extractions}
    dossiers: list[StoryDossier] = []
    dossier_dir = run_dir / "dossiers"
    dossier_dir.mkdir(exist_ok=True)
    for story in selected:
        cluster = cluster_by_id[story.cluster_id]
        sources = [
            extraction_by_id[candidate_id]
            for candidate_id in cluster.candidate_ids
            if candidate_id in extraction_by_id
            and extraction_by_id[candidate_id].extraction_status == "usable"
        ]
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
    if not dossiers:
        raise RuntimeError("No selected story had enough source support for a dossier.")
    return dossiers


def _first_sentence(text: str) -> str:
    for separator in (". ", "? ", "! "):
        if separator in text:
            return text.split(separator, 1)[0].strip() + separator.strip()
    return text[:240].strip()
