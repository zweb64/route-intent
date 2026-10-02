# Instructions for AI agents (Claude Code, Codex)

Read `SPEC.md` before every task. It defines scope, architecture, behavior contracts, and the quality bar.

## Git (owner only)
- Do NOT run any git commands: no branch, add, commit, stash, checkout, push,
  or PR creation. The owner runs all git operations.
- Work directly in the current working tree on whatever branch is checked out.
- When the task is complete, stop and provide:
  1. A list of every file created, modified, or deleted.
  2. Suggested commit(s): Conventional Commit messages, each with the files it should include.
  3. A PR title and a PR body filled in from `.github/pull_request_template.md`.
- Before stopping, run `ruff check .`, `ruff format --check .`, and `pytest`, and paste the real output.

## Engineering rules
- Work only on the current milestone. Do not add features outside SPEC.md.
- Never invent FRR output. Parser tests use fixtures captured from the lab. If fixtures are missing, say which captures are needed and stop.
- Every bug fix includes a regression test that fails before the fix.
- No `shell=True`; pass argument lists to subprocess. No secrets in code or logs.
- Do not weaken, skip, or delete a failing test to make CI pass. Report it instead.
- If the spec is ambiguous or seems wrong, list the question in the PR's "Spec deviations / open questions" section rather than guessing.
- Never modify `.github/workflows/`, `SPEC.md`, or `AGENTS.md` unless the task explicitly says to.
