# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-01-05

### Added
- Public submission endpoint accepting JSON, URL-encoded and multipart bodies.
- Per-form field schemas stored in the database (types, required, length and range limits, select options).
- Spam defences: honeypot field, signed form token with minimum time-to-submit, link / blocked-word /
  disposable-email scoring, per-IP and per-form rate limiting (in-memory or Redis).
- Per-form CORS allow-list with preflight support.
- Lead storage on SQLite or PostgreSQL, consent flag with timestamp, salted-HMAC IP hashing.
- Transactional outbox with exponential backoff, stale-lock recovery and delivery logs.
- Notifiers: HMAC-signed webhook, SMTP email, Telegram, Slack.
- Admin API with hashed API keys: form CRUD, lead search, status updates, GDPR erasure, CSV export
  (formula-injection safe), delivery logs and manual retry.
- Typer CLI: `migrate`, `create-admin-key`, `create-form`, `list-forms`, `purge-leads`, `serve`.
- Structured JSON logging, `/healthz` and `/readyz`, OpenAPI docs with examples.
- Dockerfile, docker-compose (API + PostgreSQL + Redis), Makefile and GitHub Actions CI.
