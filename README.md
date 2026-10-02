# route-intent

`route-intent` verifies that a network's live routing state matches a declared intent.
The operator writes `intent.yaml` describing expected IS-IS adjacencies, BGP sessions, routes, and
best-path exits; the tool collects live state from FRRouting routers and reports every mismatch.

**Status: in development.** Only `route-intent --version` works so far. See [SPEC.md](SPEC.md) for
scope, architecture, and milestones.

## Install for development

Requires Python 3.11 or newer.

```sh
git clone https://github.com/zweb64/route-intent.git
cd route-intent
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Check the install and run the checks CI runs:

```sh
route-intent --version
ruff check .
ruff format --check .
pytest -m "not lab"
```
