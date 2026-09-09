from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlsplit

from morning_radio.artifacts.io import atomic_write_text
from morning_radio.models import CandidateStory, Cluster, Rundown, StoryDossier
from morning_radio.showgen.script import match_story_sections, spoken_blocks


def write_sources_page(
    rundown: Rundown,
    dossiers: list[StoryDossier],
    clusters: list[Cluster],
    candidates: list[CandidateStory],
    run_dir: Path,
    final_script: str | None = None,
    run_id: str | None = None,
) -> Path:
    _ = clusters
    candidate_by_id = {item.candidate_id: item for item in candidates}
    dossier_by_id = {item.cluster_id: item for item in dossiers}
    scripted_cluster_ids = scripted_source_order(final_script, rundown, dossiers)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Episode Sources</title></head><body>",
        f"<h1>Episode Sources: {html.escape(rundown.show_date.isoformat())}</h1>",
        f"<p><strong>Run ID:</strong> {html.escape(run_id or 'unknown')}</p>",
    ]
    for cluster_id in scripted_cluster_ids:
        if cluster_id not in dossier_by_id:
            continue
        dossier = dossier_by_id[cluster_id]
        parts.append(f"<section id='{html.escape(cluster_id)}'>")
        parts.append(f"<h2>{html.escape(dossier.working_headline)}</h2><ul>")
        for candidate_id in supported_source_ids(dossier):
            candidate = candidate_by_id.get(candidate_id)
            if not candidate:
                continue
            publisher = html.escape(candidate.publisher or candidate.feed_id)
            title = html.escape(candidate.title)
            url = html.escape(candidate.url, quote=True)
            try:
                parsed_url = urlsplit(candidate.url)
                linkable = parsed_url.scheme.casefold() in {"http", "https"} and bool(
                    parsed_url.hostname
                )
            except ValueError:
                linkable = False
            source_title = f"<a href='{url}'>{title}</a>" if linkable else title
            retrieved = html.escape(candidate.retrieved_at.isoformat())
            parts.append(
                f"<li>{source_title} — {publisher} <small>retrieved {retrieved}</small></li>"
            )
        parts.append("</ul></section>")
    parts.append("</body></html>")
    path = run_dir / "sources.html"
    atomic_write_text(path, "\n".join(parts))
    return path


def scripted_source_order(
    final_script: str | None,
    rundown: Rundown,
    dossiers: list[StoryDossier],
) -> list[str]:
    story_order = [
        segment.cluster_ids[0]
        for segment in rundown.segments
        if segment.type == "story" and len(segment.cluster_ids) == 1
    ]
    if not final_script:
        return story_order
    full_story_text = "\n".join(
        text
        for _, text in spoken_blocks(final_script)
        if text.lstrip().lower().startswith(("now,", "next,", "the next story"))
    ).casefold()
    if not full_story_text:
        full_story_text = final_script.casefold()
    matches = match_story_sections(full_story_text, dossiers)
    return sorted(
        (cluster_id for cluster_id in story_order if cluster_id in matches),
        key=lambda cluster_id: full_story_text.index(matches[cluster_id]),
    )


def supported_source_ids(dossier: StoryDossier) -> list[str]:
    supported = {
        candidate_id for fact in dossier.facts for candidate_id in fact.supporting_candidate_ids
    }
    return [candidate_id for candidate_id in dossier.source_ids if candidate_id in supported]
