# Contributing

Thanks for taking the time to improve Lead Capture API.

## Development setup

```bash
git clone https://github.com/saizulamin/lead-capture-api.git
cd lead-capture-api
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,redis]"
```

Python 3.10, 3.11 and 3.12 are supported. Please avoid syntax or standard-library features newer than 3.10.

## Checks

```bash
ruff check .
ruff format --check .
mypy leadcapture
pytest
```

`make lint test typecheck` runs the same things. CI runs them on every supported Python version.

## Guidelines

- Add or update tests for every behaviour change. Notifier tests mock HTTP with `respx`; no test may touch the network.
- Keep the public submission endpoint boring for bots: spam handling must not leak which rule matched.
- Never log or store raw IP addresses or secrets. IPs are hashed with `IP_HASH_SALT`.
- Database changes need an Alembic migration in `leadcapture/migrations/versions`. A test checks that
  migrations and models stay in sync.
- New notifiers go in `leadcapture/notifiers/`: add a config model in `configs.py`, a `Notifier` subclass, register it
  in `__init__.py`, and classify failures as retryable or permanent via `DeliveryError`.
- Keep pull requests focused and describe the user-visible effect in the description. Update `CHANGELOG.md`.

## Reporting security issues

Please do not open public issues for vulnerabilities. Email hello@saizul.com with details and a way to reproduce.
