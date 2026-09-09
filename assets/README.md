# Audio Assets

Optional production assets live in these folders:

- `opening/`
- `closing/`
- `bumpers/`
- `beds/`

The default configuration marks all asset classes optional, so `./show morning --no-assets` and ordinary dry runs can produce speech-only episodes.

Export assets at a normal listening level. The mixer skips optional files below
-40 LUFS (or silent files), prints a warning, and writes a diagnostic in `mix/`.
Required assets at those levels fail the render. This prevents large gain boosts
from turning nearly silent exports into loud noise. Re-export affected files from
the original music project; increasing the volume of a noisy export cannot recover
missing musical detail.

Clips receive constant gain capped at +12 dB and peak limiting. Speech keeps its
level when a background bed is added; beds play at 18 percent of their normalized
amplitude. Final mastering limits peaks without dynamically boosting pauses.
