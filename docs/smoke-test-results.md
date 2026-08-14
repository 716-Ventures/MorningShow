# Smoke Test Results

Status: not yet run on the full target stack.

Current local verification on this checkout:

- `uv sync`: pass
- `./show --help`: pass
- `./show morning --help`: pass
- `./show doctor --help`: pass
- `uv run ruff check .`: pass
- `uv run pytest`: pass, 17 tests

Known local environment gaps from `./show doctor`:

- Ollama is not reachable at the configured URL.
- Configured model `qwen3:30b` is not available until Ollama is running with that model.
- FFmpeg is not on PATH.
- FFprobe is not on PATH.
- Kokoro is not importable in the current environment.
