---
name: codex-review
description: Run a read-only Codex review of one file, folder or commit range and write it to docs/reviews/. Use only when the owner asks for a Codex review or types /codex-review. Never start it from the loop.
---

# Codex review

Codex reviews one target read-only. `review.py` writes the review to
`docs/reviews/<date>-<topic>-codex-review.md`. Each finding in it starts with `Decision: open`.

## Steps

1. Derive one target and a topic slug from the conversation.
   - The target is one file, one folder or one commit range (for example `abc123..HEAD`). Paths are relative to the repo root.
   - Use one target per run. A large review stalls more often, so split it into several runs with narrow targets.
   - The topic slug has lowercase letters, digits and single hyphens, for example `hook-guard`.
2. Run the script from the repo root:

   ```bash
   uv run --script .agents/skills/codex-review/review.py --target <path-or-range> --topic <slug>
   ```

   On success it prints only the path of the review file.
3. If it fails (exit code not 0), show the owner the reason it printed. Do not write a review file by hand.
4. Read the review file. For each finding, replace `Decision: open` with one of:
   - `Decision: taken - <reason>`
   - `Decision: partly taken - <reason>`
   - `Decision: rejected - <reason>`

   The decisions are made by whoever works on the review: the owner together with Claude, or Claude alone.
5. Commit the review file. When Claude worked alone, commit it together with the changes Claude made for the findings.

## Adversarial review

This skill runs a normal review. For an adversarial review, point the owner to `/codex:adversarial-review` from the Codex plugin. The kit does not wrap it.
