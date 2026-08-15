# Smoke Test Results

Status: not yet run on the full target stack.

Current local verification on this checkout:

- `uv sync`: pass
- `./show --help`: pass
- `./show morning --help`: pass
- `./show doctor --help`: pass
- `uv run ruff check .`: pass
- `uv run pytest`: pass, 17 tests
- after LLM/TTS adapter work, `uv run pytest`: pass, 20 tests

Known local environment gaps from `./show doctor`:

- Ollama is not reachable at the configured URL.
- Configured model `qwen3:30b` is not available until Ollama is running with that model.
- FFmpeg is not on PATH.
- FFprobe is not on PATH.
- Kokoro is not importable in the current environment.

Fixture mode is available for local orchestration testing:

```bash
MORNING_RADIO_FIXTURE_RUN=1 MORNING_RADIO_FAKE_LLM=1 MORNING_RADIO_FAKE_TTS=1 ./show morning --minutes 10 --no-assets
```

It still requires FFmpeg/FFprobe to complete MP3 export.
