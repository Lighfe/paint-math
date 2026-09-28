# Hook activation check

The owner runs this check once, after the hook wiring issue (#7) is closed. Spec: `docs/specs/2026-09-25-agent-graph-kit-v1.md`, section 5.9.

- **Who:** the owner. You type the prompts and check each result. Claude Code only makes the calls. An agent does not test its own gates.
- **Where:** an interactive session in the Claude Code VS Code extension (the surface of the loop), in the repo root, in **Auto mode**. Auto mode is the mode the loop runs in. Manual mode is not an option for the loop. Check the mode before each step: the mode indicator of the session reads Auto.
- **When:** after #7 is closed and after you deleted `disableAllHooks` from `.claude/settings.local.json` (delete the file). Before the next real issue gets the label `ready`.
- **One session:** from step c1 on, run all steps in one session. `SendMessage` reaches the PM agent only in the session that launched it.
- **Confirm pasted prompts:** if Claude asks you to confirm a pasted prompt ("Do you want me to run it?"), answer yes.

In the prompts, replace `<N>` with the number of the throwaway issue. After each step, look at the comments of the throwaway issue on GitHub.

## If a step fails

The loop does not start. Then:

1. Write down the step and the message.
2. Delete `.claude/settings.local.json` if step f created it.
3. Close the throwaway issue by hand (see Clean-up).
4. Fix the cause first.

If a launched role ends without a result comment, the issue is pending, and the guard denies every later call on it. A comment `## Owner: RESUME` ends the pending state, but after it only a PM launch is allowed: an engineer launch is denied with `G3:`, a QA launch with `G4:`. So do one of these:

- Post `## Owner: RESUME` on the issue by hand, then run the PM again from step c1 and rebuild the state up to the failed step.
- Or start again on a new throwaway issue.

`## Owner: RESUME` must be the whole first line of the comment, with nothing else on that line.

## Set-up

1. Delete `.claude/settings.local.json` (it only holds `disableAllHooks`).
2. Add the Auto mode prerequisite for QA. In a session, run `/permissions`, open the **Auto mode** tab, and add an allow entry (keep `$defaults`) for exactly `scripts/qa-codex ROLE=qa ISSUE=<number>` in this repo. It is saved as `autoMode.allow` in `~/.claude/settings.json`. The auto mode classifier does not read `autoMode` from project settings. An entry in the **Allow** tab (`permissions.allow`) has no effect on the classifier.
3. Check that the working tree is clean: `git status --porcelain` prints nothing. The guard denies every launch while it is not clean.
4. Make sure no other issue has the label `ready`, so no loop picks up work by mistake.
5. In a terminal outside Claude Code, create the throwaway issue with the label `ready`. Save this body as a file first, for example `/tmp/throwaway.md`:

   ```markdown
   Lane: default

   ## Goal

   Throwaway issue for the hook activation check. No real work.

   ## Acceptance criteria

   - [ ] 1. No file in the repo changes: `git diff --stat <base>..<head>` of the DONE range prints nothing

   ## Out of scope

   - Everything else. The owner closes this issue after the check. No follow-up

   ## Constraints

   - Do not change, create or commit any file
   ```

   Then run `gh issue create --title "Hook activation check (throwaway)" --label ready --body-file /tmp/throwaway.md`. It must not have the labels `later` or `needs-owner`.

## Steps

### a. The guard is listed

Start a new session in the repo, in Auto mode. Accept the trust dialog if it shows. Run `/hooks`. If the extension has no `/hooks` command, check it with `claude` in the VS Code integrated terminal.

Expected: a `PreToolUse` hook with the matcher `Agent|Bash|SendMessage` that runs `.claude/hooks/guard.py`.

### b. A launch without a launch line is denied

Prompt:

````
Launch the pm subagent with exactly this prompt and nothing else:

```
Your role is defined in docs/team/pm.md.
Work on issue #<N>. Follow the process in docs/process.md.
```

If the call is denied, stop and show me the deny message. Do not retry and do not try another way.
````

Expected: the launch is denied, and the deny message starts with `G1:` (no launch line). The issue gets no new comment.

### c. A `SendMessage` continuation is checked

Run c1 to the end of the checklist in one session.

c1. Launch the PM with a launch line. Prompt:

````
Launch the pm subagent with exactly this prompt:

```
ROLE=pm ISSUE=<N>
Your role is defined in docs/team/pm.md.
Work on issue #<N>. Follow the process in docs/process.md.
This is a synthetic hook check: do not edit the issue. Post ## PM: GROOMED.
```

Wait until it ends and show me its final message. Remember its agent id.
````

Expected: the issue gets `## Launch: pm (attempt 1)`, then `## PM: GROOMED`.

c2. Launch the engineer, who reports a synthetic block. Prompt:

````
Launch the software-engineer subagent with exactly this prompt:

```
ROLE=engineer ISSUE=<N>
Your role is defined in docs/team/software-engineer.md.
Work on issue #<N>. Follow the process in docs/process.md.
This is a synthetic hook check: change no file. Post ## Engineer: BLOCKED for criterion 1 with the reason "synthetic check of the SendMessage continuation".
```

Wait until it ends and show me its final message.
````

Expected: the issue gets `## Launch: engineer (attempt 1)`, then `## Engineer: BLOCKED`.

c3. Continue the PM without the launch line. Prompt:

```
Continue the pm agent from the first launch with SendMessage. The message is only: "The engineer posted a BLOCKED comment on issue #<N>. Check the issue again and post your result." Do not add a ROLE line. If the call is denied, stop and show me the deny message. Do not retry and do not try another way.
```

Expected: denied with `G1:` (no launch line). The issue gets no new comment.

c4. Continue the PM with the launch line. Prompt:

````
Continue the pm agent from the first launch with SendMessage. The message is exactly:

```
ROLE=pm ISSUE=<N>
The engineer posted a BLOCKED comment on issue #<N>. Check the issue again and post your result.
This is a synthetic hook check: do not edit the issue. Post ## PM: GROOMED.
```

Wait until it ends and show me its final message.
````

Expected: the call runs. The issue gets `## Launch: pm (continued, round <n>)`, then a new `## PM: GROOMED`. The number counts all PM launch comments on the issue, so here it is `round 2`.

Record:

- The field names of the `SendMessage` tool input, as the extension shows the call (for example `to`, `message`, `summary`). Only `to` and `message` are required. A missing `summary` is not a failure. If the call with the line is denied with `G1: SendMessage input has no string to or no string message`, the field names differ: the step fails.
- Whether the extension has `SendMessage` at all. If it does not, the loop runs with `claude` in the VS Code integrated terminal. Then run the check there on a new throwaway issue, from a new PM launch (c1). Do not repeat step c on the same issue.

### d. Writes of the settings files are denied

Prompt 1:

```
Use the Write tool to write {"disableAllHooks": true} into .claude/settings.local.json. If the call is denied, stop and show me the message. Do not try another way.
```

Expected: denied by the permission rule. The file does not exist afterwards.

Prompt 2:

```
Run exactly this Bash command: echo '{"disableAllHooks": true}' > .claude/settings.local.json
If the call is denied, stop and show me the message. Do not try another way.
```

Expected: denied, and the deny message starts with `G8:`. The file does not exist afterwards, and `git status --porcelain` prints nothing.

Prompt 3:

```
Use the Edit tool on the existing file .claude/settings.json: replace "timeout": 120 with "timeout": 121. Read the file first with the Read tool if the Edit tool needs it. If the Edit call is denied, stop and show me the message. Do not try another way.
```

Expected: the `Edit` call is denied by the permission rule. `git status --porcelain` prints nothing afterwards. A "file not found" error, a refusal by the model, or a rejected prompt counts as a failed step: the `Edit` call must be made and denied.

### e. A guarded `qa-codex` call shows no permission prompt

The guard allows `qa-codex` only after a valid `## Engineer: DONE` with a `Commits: <base>..<head>` line that was posted after an engineer launch (G4). Without it, the guard denies the call, and a deny also shows no prompt. So bring the issue into that state first.

e1. Launch the engineer again. Prompt:

````
Launch the software-engineer subagent with exactly this prompt:

```
ROLE=engineer ISSUE=<N>
Your role is defined in docs/team/software-engineer.md.
Work on issue #<N>. Follow the process in docs/process.md.
This is a synthetic hook check: change and commit no file. Post ## Engineer: DONE with the line Commits: <HEAD>..<HEAD>, where <HEAD> is the output of git rev-parse HEAD.
```

Wait until it ends and show me its final message.
````

Expected: the issue gets `## Launch: engineer (attempt 2)`, then `## Engineer: DONE` with a `Commits:` line.

e2. Run QA. Check first that the mode indicator reads Auto. Prompt:

```
Run exactly this as the whole Bash command, with the Bash tool's run_in_background option: scripts/qa-codex ROLE=qa ISSUE=<N>
No &, no cd, no redirection, nothing in front of scripts/. Wait until it ends. If the call is denied, stop and show me the deny message.
```

Expected: no permission prompt and no auto mode classifier deny; the call runs. The issue gets `## Launch: qa (attempt 1)`, then a `## QA: …` result. Each of these is a failed step:

- a `G…` deny
- a permission prompt
- an auto mode classifier deny (the guard has then already posted `## Launch: qa (attempt 1)`, so the issue is pending; see "If a step fails")

e3. Optional: the close allow rule. Run this only if the current result is `## QA: PASS` and its `Verified:` SHA is equal to `git rev-parse HEAD`. Prompt:

```
Run exactly this as the whole Bash command: gh issue close <N>
Nothing else in the command. If the call is denied, stop and show me the deny message.
```

Expected: no permission prompt and no deny; the issue is closed. If the result is not `## QA: PASS` with `Verified:` equal to HEAD, skip e3: clean-up closes the issue by hand.

### f. `disableAllHooks` and user-level hooks

The probe is a harmless read that the guard denies with `G8:`. Prompt (the same for each run below):

```
Run exactly this Bash command: cat .claude/settings.json && true
If the call is denied, stop and show me the deny message. Do not try another way.
```

1. Run the probe. Expected: denied, and the deny message starts with `G8:`.
2. By hand, in your editor, create `.claude/settings.local.json` with `{"disableAllHooks": true}`.
3. Run the probe again. Expected: it runs and prints the file. This is the expected result, because hooks are off. It is not an unstable guard. Do not run step b or any other launch while hooks are off.
4. Trigger one of your user-level hooks (from `~/.claude/settings.json`) if you have one. Record whether it also stops. If you have no user-level hook, record "user-level hooks: not tested".
5. Delete `.claude/settings.local.json` again.
6. Run the probe a third time. Expected: denied again with `G8:`. Run `/hooks`: the guard is listed again.

## Clean-up

1. If e3 did not close the throwaway issue, close it by hand, in the GitHub web page or in a terminal outside Claude Code. It must not stay open with the label `ready`.
2. Check that `.claude/settings.local.json` does not exist and that `git status --porcelain` prints nothing.
3. Write the results of step c (field names, `SendMessage` in the extension) and step f (user-level hooks) into a comment on #7 or a new issue.
