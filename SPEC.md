# route-intent — Project Spec

Read this file before every task. It is the source of truth for scope, architecture, and quality bar.

## 1. Purpose

`route-intent` verifies that a network's live routing state matches a declared intent.
The operator writes `intent.yaml` describing expected IS-IS adjacencies, BGP sessions, routes, and
best-path exits; the tool collects live state from FRRouting routers and reports every mismatch.

Target v1.0.0: a packaged, tested, documented CLI validated against a real 5-router containerlab topology,
including scripted failure scenarios the tool detects.

## 2. Lab (lives in `lab/`)

- containerlab + FRRouting (`quay.io/frrouting/frr`), run on an Ubuntu VM.
- Topology:
  - AS 65001: r1, r2, r3 — IS-IS level-2 for internal reachability; iBGP on loopbacks, r1 is route reflector.
  - AS 65002: r4 — eBGP to r1 AND r2 (dual-homed). Advertises 192.0.2.0/24.
  - AS 65003: r5 — eBGP to r3. Advertises 198.51.100.0/24.
  - Links: r1–r2, r2–r3, r1–r3, r1–r4, r2–r4, r3–r5.
  - Loopbacks: r1 10.0.0.1/32, r2 10.0.0.2/32, r3 10.0.0.3/32, r4 10.0.0.4/32, r5 10.0.0.5/32.
  - Point-to-point links use /31s from 10.1.0.0/24, documented in `lab/README.md`.
  - Policy: on AS 65001, prefer exits via r1 for 192.0.2.0/24 (local-preference set on r1's eBGP import).
- Files: `lab/topology.clab.yml`, `lab/configs/rN/frr.conf` + `daemons`, `lab/README.md` (deploy/destroy steps).
- `scenarios/`: shell scripts that break one thing each and a matching `restore` script:
  1. `link-down-r1-r4` — r1–r4 link down → path preference to 192.0.2.0/24 must FAIL.
  2. `bgp-session-down-r3-r5` — shut BGP neighbor → session + route checks FAIL.
  3. `isis-adjacency-down-r2-r3` — passive/shut IS-IS on interface → adjacency check FAILS.
  4. `localpref-removed` — remove r1 local-pref policy → path preference may FAIL (document observed behavior).

## 3. Architecture

```
intent.yaml → Intent Loader → validated Intent models
routers     → Transport → Collector → raw FRR JSON → Parsers → normalized State models
Engine: per-device collection with bounded concurrency + per-device failure isolation
Checks: pure functions (Intent, State) → CheckResult(PASS | FAIL | ERROR, expected, actual, detail)
Reporters: human-readable table + deterministic JSON
```

Package layout (`src/route_intent/`):
- `intent.py` — Pydantic v2 models + `load_intent(path) -> Intent`
- `transport.py` — `Transport` protocol; `DockerExecTransport` (runs `docker exec <container> vtysh -c "<cmd>"`)
- `collector.py` — runs the FRR JSON commands per device via a Transport
- `parsers.py` — FRR JSON → state models (`IsisAdjacencyState`, `BgpSessionState`, `RouteState`, `BestPathState`)
- `checks.py` — one function per intent type
- `engine.py` — orchestration, `ThreadPoolExecutor` with max workers, isolation
- `report.py` — human + JSON output
- `cli.py` — entry point `route-intent verify --intent intent.yaml [--json] [--workers N] [--timeout S]`

FRR commands (all `json` variants): `show isis neighbor json`, `show bgp summary json`,
`show ip route json`, `show bgp ipv4 unicast <prefix> json`.

## 4. Intent rules (validated at load time)

1. Every device reference exists in `devices` (error names the path, e.g. `bgp_sessions[2].peer`).
2. `ibgp` requires equal ASNs; `ebgp` requires different ASNs.
3. No self-references (adjacency/session/exit_via to self).
4. No duplicates; adjacencies compared order-independently.
5. ASN in 1–4294967295.
6. Prefixes are valid IPv4 networks; host bits set → reject with a clear error.
7. Unknown keys rejected (`extra="forbid"`).
8. Loader raises one typed `IntentError` for: missing file, unreadable file, invalid YAML, empty file, non-mapping root, validation failure.
A session declared from both ends is allowed (each side is checked independently).

## 5. Behavior contracts

- Exit codes: 0 all PASS; 1 any FAIL; 2 any ERROR (bad intent, unreachable device, usage). ERROR outranks FAIL.
- A device that cannot be collected yields ERROR results for its checks only; other devices continue.
- Per-device timeout enforced; a hung device never blocks the run beyond the timeout.
- JSON output is deterministic: stable key order, results sorted by (device, check_type, subject).
- Write/flush failures on stdout (e.g. closed pipe) → error to stderr, exit 2. Never report success when output was lost.
- No secrets in logs. No shell injection: transport passes argument lists, never formatted shell strings.

## 6. Testing

- Parser tests use REAL JSON captured from the lab, stored in `tests/fixtures/frr/<scenario>/<device>/<command>.json`.
  Never hand-invent FRR output. If fixtures are missing, stop and say which captures are needed.
- `scripts/capture_fixtures.sh` captures all commands from all routers into a named fixture set.
- Unit tests: every intent rule (one failing case each, asserting the field path), every parser, every check, engine isolation and timeout (with a fake Transport), reporters, CLI exit codes, closed-pipe handling.
- Integration tests (marked `@pytest.mark.lab`, skipped unless `ROUTE_INTENT_LAB=1`): baseline all PASS; each scenario produces the expected FAIL set.
- Regression rule: every bug fixed gets a test that fails before the fix.

## 7. Quality bar / definition of done (per milestone)

- `ruff check` and `pytest` clean; CI (GitHub Actions, Python 3.11 and 3.12) green.
- Small, reviewable commits with clear messages; one branch + PR per milestone.
- Docs updated in the same PR (`README.md`, `docs/design.md`).
- Before release: build wheel + sdist, install the wheel into a fresh venv, run the installed `route-intent --version` and a fixture-based smoke test, verify version metadata matches the tag.

## 8. Milestones

| # | Deliverable | Acceptance |
|---|---|---|
| M0 | Lab files + README | `containerlab deploy` → all IS-IS adjacencies Up, all BGP sessions Established, policy verified manually |
| M1 | `intent.py` + tests + `examples/intent.yaml` matching the lab | All rule tests pass; example loads |
| M2 | Fixture capture script, baseline + scenario fixtures, `parsers.py` + tests | Parsers pass on real fixtures |
| M3 | `checks.py` + tests | Each check type PASS/FAIL covered with fixture-derived state |
| M4 | `transport.py`, `collector.py`, `engine.py` + tests | Isolation and timeout proven with fake transport |
| M5 | `report.py`, `cli.py` + tests | Exit codes, deterministic JSON, closed-pipe behavior tested |
| M6 | `scenarios/` + lab integration tests | Baseline PASS; each scenario detected |
| M7 | README (with demo output), `docs/design.md`, CI, release | v1.0.0 tag + GitHub Release with wheel/sdist |

## 9. Out of scope for v1

SSH transport, non-FRR vendors, IPv6, config push/remediation, web UI.
