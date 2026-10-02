# Instructions for AI agents (Claude Code, Codex)

Read `SPEC.md` before every task. It defines scope, architecture, behavior contracts, and the quality bar.

## Git workflow (mandatory)
- Never commit or push to `main`. `main` is protected; only the owner merges.
- One branch per milestone or fix: `m<N>-<short-name>` (e.g. `m1-intent-models`) or `fix/<short-name>`.
- Small commits, Conventional Commit messages: `feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`.
- Before opening a PR: run `ruff check .`, `ruff format --check .`, and `pytest -m "not lab"`; all must pass.
- Open a PR using the template. Fill in acceptance criteria and paste real test output.
- Then STOP. Do not start the next milestone until the owner merges.

## Engineering rules
- Work only on the current milestone. Do not add features outside SPEC.md.
- Never invent FRR output. Parser tests use fixtures captured from the lab. If fixtures are missing, say which captures are needed and stop.
- Every bug fix includes a regression test that fails before the fix.
- No `shell=True`; pass argument lists to subprocess. No secrets in code or logs.
- Do not weaken, skip, or delete a failing test to make CI pass. Report it instead.
- If the spec is ambiguous or seems wrong, list the question in the PR's "Spec deviations / open questions" section rather than guessing.
- Never modify `.github/workflows/`, `SPEC.md`, or `AGENTS.md` unless the task explicitly says to.
