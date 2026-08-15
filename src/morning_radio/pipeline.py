from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from morning_radio import db
from morning_radio.artifacts.runs import RunContext, create_run
from morning_radio.artifacts.sources import write_sources_page
from morning_radio.audio.master import mix_and_master
from morning_radio.audio.production import build_production_plan, synthesize_script
from morning_radio.llm.client import build_llm_client
from morning_radio.models import CandidateStory, ExtractionResult, StageStatus
from morning_radio.newsroom.cluster import cluster_stories
from morning_radio.newsroom.dossier import build_dossiers
from morning_radio.newsroom.extract import extract_articles
from morning_radio.newsroom.feeds import discover_candidates
from morning_radio.newsroom.score import score_stories
from morning_radio.newsroom.select import select_stories
from morning_radio.profile.compiler import load_profile
from morning_radio.settings import (
    load_app_settings,
    load_feed_settings,
    load_production_settings,
    repo_root,
)
from morning_radio.showgen.rundown import build_rundown
from morning_radio.showgen.script import write_script
from morning_radio.showgen.verify import verify_script


def run_morning(requested_date: date, minutes: int | None, no_assets: bool = False) -> dict[str, str | int]:
    root = repo_root()
    db.initialize(root / "data" / "app.db")
    profile = load_profile(root)
    app_settings = load_app_settings(root)
    feed_settings = load_feed_settings(root)
    production_settings = load_production_settings(root)
    target_minutes = minutes or profile.show_format.target_minutes
    context = create_run(requested_date, target_minutes, root)
    llm = build_llm_client(app_settings.llm, context.run_dir)
    try:
        return _run_pipeline(
            context,
            requested_date,
            target_minutes,
            no_assets,
            profile,
            app_settings,
            feed_settings,
            production_settings,
            llm,
        )
    except Exception as exc:
        if context.record.status is not StageStatus.FAILED:
            context.fail(context.record.status.value, str(exc))
        raise


def _run_pipeline(
    context: RunContext,
    requested_date: date,
    target_minutes: int,
    no_assets: bool,
    profile,
    app_settings,
    feed_settings,
    production_settings,
    llm,
) -> dict[str, str | int]:
    context.register_artifact("profile_snapshot", _write_profile_snapshot(context.run_dir, profile))
    context.transition(StageStatus.DISCOVERING)
    if os.environ.get("MORNING_RADIO_FIXTURE_RUN") == "1":
        candidates, extractions = _fixture_news(context.run_dir)
    else:
        candidates = discover_candidates(feed_settings, app_settings, context.run_dir)
        extractions = []
    context.register_artifact("candidates", context.run_dir / "candidates.jsonl")

    context.transition(StageStatus.EXTRACTING)
    if not extractions:
        extractions = extract_articles(candidates, app_settings, context.run_dir)
    context.register_artifact("extracted", context.run_dir / "extracted")

    context.transition(StageStatus.CLUSTERING)
    clusters = cluster_stories(candidates, extractions, context.run_dir)
    context.register_artifact("clusters", context.run_dir / "clusters.json")

    context.transition(StageStatus.SCORING)
    scores = score_stories(clusters, profile, context.run_dir, llm)
    selected = select_stories(scores, profile, app_settings, context.run_dir)
    context.register_artifact("scored_stories", context.run_dir / "scored-stories.json")
    context.register_artifact("selected_stories", context.run_dir / "selected-stories.json")

    context.transition(StageStatus.RESEARCHING)
    dossiers = build_dossiers(selected.selected, clusters, extractions, context.run_dir, llm)
    context.register_artifact("dossiers", context.run_dir / "dossiers")

    context.transition(StageStatus.PLANNING)
    rundown = build_rundown(requested_date, target_minutes, profile, dossiers, context.run_dir, llm)
    context.register_artifact("rundown", context.run_dir / "rundown.json")

    context.transition(StageStatus.WRITING)
    script = write_script(profile, rundown, dossiers, context.run_dir, llm)
    context.register_artifact("script_draft", context.run_dir / "script-draft.md")

    context.transition(StageStatus.VERIFYING)
    verification = verify_script(script, dossiers, context.run_dir, llm)
    context.register_artifact("verification", context.run_dir / "verification.json")
    if verification.status != "pass":
        raise RuntimeError("Verification failed with high-severity issues.")
    context.register_artifact("script_final", context.run_dir / "script-final.md")

    context.transition(StageStatus.SYNTHESIZING)
    audio = synthesize_script(script, production_settings, context.run_dir)
    plan = build_production_plan(script, audio, context.run_dir, no_assets)
    context.register_artifact("raw_audio", context.run_dir / "raw-audio")
    context.register_artifact("production_plan", context.run_dir / "production-plan.json")

    context.transition(StageStatus.MIXING)
    episode = mix_and_master(plan, production_settings, context.run_dir)
    sources = write_sources_page(rundown, dossiers, clusters, candidates, context.run_dir)
    context.register_artifact("episode", episode)
    context.register_artifact("sources", sources)
    db.update_story_history(
        context.root / "data" / "app.db",
        [(cluster.fingerprint, cluster.canonical_title) for cluster in clusters if cluster.cluster_id in {d.cluster_id for d in dossiers}],
        requested_date.isoformat(),
    )
    context.complete()
    return {
        "episode": str(episode.relative_to(context.root)),
        "sources": str(sources.relative_to(context.root)),
        "run_id": context.record.run_id,
        "stories": len(dossiers),
        "target_minutes": target_minutes,
    }


def _write_profile_snapshot(run_dir: Path, profile) -> Path:
    path = run_dir / "profile-snapshot.json"
    path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _fixture_news(run_dir: Path) -> tuple[list[CandidateStory], list[ExtractionResult]]:
    import json
    from datetime import datetime

    now = datetime.now().astimezone()
    topics = [
        ("ai", "OpenAI releases a new coding agent workflow", "technology"),
        ("ai", "Anthropic expands enterprise AI controls", "technology"),
        ("apple", "Apple updates developer tools for macOS", "technology"),
        ("world", "Major world leaders agree to climate framework", "world"),
        ("us", "Congress advances major infrastructure package", "us"),
        ("buffalo", "Buffalo approves waterfront transit funding", "local"),
        ("bills", "Bills adjust offensive line after injury", "sports"),
        ("ai", "New open model improves local inference", "technology"),
        ("apple", "Swift package tooling gets faster resolver", "technology"),
        ("world", "Central banks signal coordinated inflation response", "world"),
        ("buffalo", "Western New York prepares for lake-effect storm", "local"),
        ("noise", "Celebrity couple announces lifestyle brand", "entertainment"),
        ("ai", "AI startup raises funding for developer testing", "technology"),
        ("us", "Supreme Court issues significant privacy ruling", "us"),
        ("world", "Global health agency tracks new outbreak response", "world"),
        ("bills", "Routine Bills practice notes from training camp", "sports"),
        ("buffalo", "Local schools adopt new phone policy", "local"),
        ("apple", "Apple security update patches active exploit", "technology"),
        ("ai", "Researchers publish safer agent evaluation method", "technology"),
        ("world", "Major shipping route reopens after disruption", "world"),
    ]
    candidates: list[CandidateStory] = []
    extractions: list[ExtractionResult] = []
    extracted_dir = run_dir / "extracted"
    extracted_dir.mkdir(exist_ok=True)
    for index, (_, title, category) in enumerate(topics, start=1):
        candidate = CandidateStory(
            candidate_id=f"fixture-{index:03d}",
            feed_id=f"fixture-{category}",
            title=title,
            url=f"https://example.com/news/{index}",
            published_at=now,
            retrieved_at=now,
            publisher=f"Fixture {category.title()}",
            feed_summary=title,
            category_hints=[category],
            geography_hints=["Buffalo"] if category in {"local", "sports"} else [],
        )
        text = (
            f"{title}. This fixture article provides sourced context for the morning radio "
            f"pipeline. It explains what changed, why it matters, and what remains uncertain. "
            * 35
        )
        extraction = ExtractionResult(
            candidate_id=candidate.candidate_id,
            url=candidate.url,
            final_url=candidate.url,
            http_status=200,
            title=title,
            published_at=now,
            text=text,
            word_count=len(text.split()),
            extraction_status="usable",
        )
        candidates.append(candidate)
        extractions.append(extraction)
        (extracted_dir / f"{candidate.candidate_id}.json").write_text(
            extraction.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    with (run_dir / "candidates.jsonl").open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False) + "\n")
    return candidates, extractions
