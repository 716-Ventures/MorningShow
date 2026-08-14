from __future__ import annotations

import html
from pathlib import Path

from morning_radio.models import CandidateStory, Cluster, Rundown, StoryDossier


def write_sources_page(
    rundown: Rundown,
    dossiers: list[StoryDossier],
    clusters: list[Cluster],
    candidates: list[CandidateStory],
    run_dir: Path,
) -> Path:
    candidate_by_id = {item.candidate_id: item for item in candidates}
    cluster_by_id = {item.cluster_id: item for item in clusters}
    dossier_by_id = {item.cluster_id: item for item in dossiers}
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Episode Sources</title></head><body>",
        f"<h1>Episode Sources: {html.escape(rundown.show_date.isoformat())}</h1>",
    ]
    for segment in rundown.segments:
        for cluster_id in segment.cluster_ids:
            if cluster_id not in dossier_by_id:
                continue
            dossier = dossier_by_id[cluster_id]
            cluster = cluster_by_id[cluster_id]
            parts.append(f"<section id='{html.escape(cluster_id)}'>")
            parts.append(f"<h2>{html.escape(dossier.working_headline)}</h2><ul>")
            for candidate_id in cluster.candidate_ids:
                candidate = candidate_by_id.get(candidate_id)
                if not candidate:
                    continue
                publisher = html.escape(candidate.publisher or candidate.feed_id)
                title = html.escape(candidate.title)
                url = html.escape(candidate.url, quote=True)
                retrieved = html.escape(candidate.retrieved_at.isoformat())
                parts.append(
                    f"<li><a href='{url}'>{title}</a> — {publisher} "
                    f"<small>retrieved {retrieved}</small></li>"
                )
            parts.append("</ul></section>")
    parts.append("</body></html>")
    path = run_dir / "sources.html"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
