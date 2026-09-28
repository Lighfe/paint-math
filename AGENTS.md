# paint-math

paint-math is a web app: the user enters an equation and a canvas draws its curve.
The frontend is the `frontend/` submodule, built by Lovable. Hooks in `.claude/hooks/` check the handoff calls (see `docs/process.md`).

Lovable project: 68a854ba-5bd5-4a8f-a15e-2d90bd84f103

## Documents

- `docs/process.md` - how work is organized. Read it before you start a task.
- `docs/task-template.md` - the template for a groomed issue
- `docs/team/` - the role definitions (orchestrator, PM, software engineer, QA engineer)
- `docs/specs/` - the design of the kit

## Commands

- `gh issue list --state open --label ready --search "-label:later -label:needs-owner"` - list the issues the loop may work on
- `gh issue view <number> --comments` - read an issue and its comments
- `gh issue comment <number> --body-file <file>` - add a comment to an issue
- `gh issue edit <number> --add-label <label>` / `--remove-label <label>` - change labels
- `gh issue close <number>` - close an issue (orchestrator only, see `docs/team/orchestrator.md`)

Test command: uv run --with pytest pytest
Never report tests as passed if no test ran.

## Skills and subagents

- Project skills go to `.agents/skills/<name>/SKILL.md`. `.claude/skills` is a symlink to `.agents/skills`.
- Subagent definitions go to `.claude/agents/`. They point to the role files in `docs/team/`.
- When the owner asks for a Codex review, use the `codex-review` skill.

## Rules

### Folders

- Specs go to `docs/specs/`. Plans go to `docs/plans/`.
- These paths override the default paths of the superpowers skills.

### Superpowers

Use these skills:

- brainstorming, writing-plans (upstream: idea → spec → plan → issues). Plan tasks become issues in `docs/task-template.md` format (see Intake in `docs/process.md`).
- test-driven-development (technique for the engineer role)
- verification-before-completion (prose version of the "done" gate)
- requesting-code-review, receiving-code-review (technique for the reviewer role; the reviewer role is not defined yet, do not invent it)
- using-git-worktrees (parallel mode; parallel mode is not defined yet, do not invent it)

Do not use these skills: subagent-driven-development, executing-plans.

Reason: these two skills are a second orchestrator. They make their own rulings without asking the human. This conflicts with `docs/process.md`, which defines when to escalate to the owner.

If a skill offers one of these two skills as the next step, do not accept. Turn the plan into issues and follow `docs/process.md`. If conflicts repeat, copy the used skills into the kit (fork later, only with evidence).

### Public repo

- Never put secrets in commits, issue bodies, issue comments or reports. `TYPESAFE_API_KEY` and other keys stay in the user environment. Redact sensitive output. Use synthetic examples.
- Do not commit full third-party articles. `docs/references/local/` is local only.
