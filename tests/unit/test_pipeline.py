from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic import HttpUrl

from morning_radio import db, pipeline
from morning_radio.artifacts.runs import create_run
from morning_radio.audio.production import SpeechItem
from morning_radio.models import (
    AudioMetadata,
    CandidateStory,
    Cluster,
    DossierFact,
    ExtractionResult,
    Rundown,
    RundownSegment,
    StoryDossier,
    VerificationResult,
    VerifiedScript,
)
from morning_radio.profile.compiler import default_profile
from morning_radio.settings import (
    AppSettings,
    FeedSettings,
    LLMSettings,
    NewsSettings,
    ProductionSettings,
    SelectionSettings,
    VerificationSettings,
)


class RecordingLLM:
    model = "test"

    def generate_text(self, *args, **kwargs) -> str:
        raise AssertionError("RecordingLLM.generate_text should not be called")

    def generate_structured(self, *args, **kwargs):
        raise AssertionError("RecordingLLM.generate_structured should not be called")


def test_pipeline_synthesizes_verified_final_script(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db.initialize(data_dir / "app.db")
    context = create_run(date(2026, 8, 15), 10, tmp_path)

    profile = default_profile()
    app_settings = AppSettings(
        llm=LLMSettings(base_url=HttpUrl("http://ollama.test"), model="test", timeout_seconds=5),
        news=NewsSettings(
            request_timeout_seconds=5,
            max_html_bytes=10240,
            minimum_article_words=1,
            minimum_usable_articles=1,
            candidate_max_age_hours=48,
            max_candidates=10,
            max_articles_to_extract=5,
            concurrency=2,
        ),
        selection=SelectionSettings(
            major_news_importance_threshold=80,
            dossier_source_preference=2,
            maximum_selected_stories=3,
        ),
        verification=VerificationSettings(maximum_correction_cycles=2),
    )
    production_settings = ProductionSettings.model_validate(
        {
            "tts": {"engine": "tone", "voice": "tone", "secondary_voice": "tone", "speed": 1.0},
            "audio": {
                "bitrate_kbps": 128,
                "sample_rate_hz": 44100,
                "channels": 1,
                "loudness_target_lufs": -16,
            },
            "assets": {
                "opening_optional": True,
                "closing_optional": True,
                "bumpers_optional": True,
                "beds_optional": True,
            },
        }
    )
    candidate = CandidateStory(
        candidate_id="candidate-001",
        feed_id="feed",
        title="Story",
        url="https://example.com/story",
        retrieved_at=datetime.now().astimezone(),
    )
    extraction = ExtractionResult(
        candidate_id="candidate-001",
        url="https://example.com/story",
        final_url="https://example.com/story",
        title="Story",
        text="Story text",
        word_count=2,
        extraction_status="usable",
    )
    cluster = Cluster(
        cluster_id="cluster-001",
        canonical_title="Story",
        candidate_ids=["candidate-001"],
        source_count=1,
        topic_hints=[],
        fingerprint="fingerprint",
    )
    dossier = StoryDossier(
        cluster_id="cluster-001",
        working_headline="Story",
        what_happened="Fact.",
        what_is_new_today="New.",
        why_it_matters="Matters.",
        background_needed="None.",
        facts=[DossierFact(claim="Fact.", supporting_candidate_ids=["candidate-001"])],
        recommended_seconds=60,
        source_ids=["candidate-001"],
    )
    rundown = Rundown(
        show_date=date(2026, 8, 15),
        target_seconds=600,
        planned_seconds=600,
        segments=[
            RundownSegment(
                segment_id="open",
                type="opening",
                title="Opening",
                cluster_ids=[],
                planned_seconds=600,
                purpose="test",
            )
        ],
    )
    synthesized_scripts: list[str] = []
    planned_scripts: list[str] = []
    mix_kwargs: dict[str, object] = {}
    order: list[str] = []

    monkeypatch.setattr(pipeline, "discover_candidates", lambda *args: [candidate])
    monkeypatch.setattr(pipeline, "extract_articles", lambda *args: [extraction])
    monkeypatch.setattr(pipeline, "cluster_stories", lambda *args: [cluster])
    monkeypatch.setattr(pipeline, "score_stories", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        pipeline,
        "select_stories",
        lambda *args, **kwargs: SimpleNamespace(selected=[], not_selected_high_score=[]),
    )
    monkeypatch.setattr(pipeline, "build_dossiers", lambda *args: [dossier])
    monkeypatch.setattr(pipeline, "build_rundown", lambda *args: rundown)
    monkeypatch.setattr(pipeline, "write_script", lambda *args: "[HOST]\nDraft text.\n")
    monkeypatch.setattr(
        pipeline,
        "verify_script",
        lambda *args, **kwargs: VerifiedScript(
            verification=VerificationResult(status="pass"),
            script="[HOST]\nCorrected final text.\n",
        ),
    )

    def synthesize(script: str, *args, **kwargs) -> list[AudioMetadata]:
        order.append("synthesize")
        synthesized_scripts.append(script)
        return [
            AudioMetadata(
                voice="tone",
                text_hash="hash",
                duration_seconds=2.0,
                path=context.run_dir / "raw-audio" / "001-host.wav",
            )
        ]

    def build_text_plan(script: str, *args) -> list[SpeechItem]:
        order.append("plan")
        planned_scripts.append(script)
        return [SpeechItem(text="Draft text.", host="HOST")]

    def attach_audio(plan, audio):
        order.append("attach")
        return [
            SpeechItem(
                text="Draft text.",
                host="HOST",
                path=context.run_dir / "raw-audio" / "001-host.wav",
                duration_seconds=2.0,
            )
        ]

    def mix(plan, production, run_dir, **kwargs):
        mix_kwargs.update(kwargs)
        return context.run_dir / "episode.mp3"

    monkeypatch.setattr(pipeline, "synthesize_script", synthesize)
    monkeypatch.setattr(pipeline, "build_text_production_plan", build_text_plan)
    monkeypatch.setattr(pipeline, "attach_audio_to_plan", attach_audio)
    monkeypatch.setattr(pipeline, "write_production_plan", lambda *args: None)
    monkeypatch.setattr(pipeline, "mix_and_master", mix)
    monkeypatch.setattr(
        pipeline,
        "write_sources_page",
        lambda *args, **kwargs: context.run_dir / "sources.html",
    )

    pipeline._run_pipeline(
        context,
        date(2026, 8, 15),
        10,
        no_assets=True,
        profile=profile,
        app_settings=app_settings,
        feed_settings=FeedSettings(feeds=[]),
        production_settings=production_settings,
        llm=RecordingLLM(),
    )

    assert synthesized_scripts == ["[HOST]\nCorrected final text.\n"]
    assert planned_scripts == ["[HOST]\nCorrected final text.\n"]
    assert order[:3] == ["plan", "synthesize", "attach"]
    assert mix_kwargs["planned_seconds"] == 2
