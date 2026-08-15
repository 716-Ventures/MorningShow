# Seven-Day Playtest

This playtest is the gate for deciding whether Personal Morning Radio is worth production-system design.

For seven consecutive mornings:

1. Run `./show doctor`.
2. Run `./show morning --minutes 20` before normal morning listening.
3. Listen without manually editing intermediate artifacts.
4. Run `./show feedback`.
5. Record whether you chose the generated show over a normal podcast/news source.
6. Record whether you finished the episode.
7. Note the most valuable segment.
8. Note any wasted segment.
9. Note anything factually questionable.
10. Note anything repetitive.
11. Note anything important that was missing.
12. Note whether the host or production became tiring.
13. Note whether yesterday's feedback changed today's show.

Success criteria:

- At least 5 of 7 episodes are substantially listened to rather than abandoned for quality.
- At least 4 of 7 mornings the generated show is chosen over a normal alternative, or would have been absent testing obligations.
- No episode contains an uncorrected high-severity factual error discovered during review.
- By Day 7, at least two durable feedback preferences visibly affect later prompts or editorial choices.
- The show feels coherent rather than like a playlist of article summaries.

After Day 7, summarize:

- total episodes generated
- total episodes completed
- most common failure modes
- durable editorial memory changes
- factual issues
- production fatigue notes
- recommendation: continue, redesign, or stop
