# Mac Studio Smoke Test

Run this on the target Apple Silicon Mac Studio after installing local dependencies.

```bash
uv sync
./show doctor
./show configure
cat data/profile.json
cat data/profile.md
./show morning --minutes 10 --no-assets
open runs
./show feedback
```

Expected behavior:

1. `./show doctor` prints every check and exits non-zero until Ollama, the configured model, Kokoro, FFmpeg, and FFprobe are available.
2. `./show configure` writes `data/profile.json` and `data/profile.md` only after confirmation.
3. `./show morning --minutes 10 --no-assets` creates a dated run directory and writes all intermediate artifacts.
4. A successful run prints `episode.mp3` and `sources.html`.
5. `./show feedback` attaches feedback to the latest completed run and appends durable preferences to `data/editorial-memory.md`.

Record the first live run in `docs/smoke-test-results.md`.
