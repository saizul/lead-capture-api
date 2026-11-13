# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Lead tags and an internal note: `PATCH /v1/admin/leads/{id}` now accepts `tags`, `add_tags`,
  `remove_tags` and `note` alongside `status`; repeatable `tag` filter on lead lists and the CSV
  export; `GET /v1/admin/tags` with per-tag lead counts; `POST /v1/admin/leads/bulk-update` to set a
  status or add/remove tags on up to 500 leads at once (all-or-nothing). Migration `0002` adds the
  `leads.tags` and `leads.note` columns (run `leadcapture migrate`).
- CI and package metadata now cover Python 3.13.
- `GET /v1/admin/stats/spam-rate`: daily spam counts and spam rate per form plus an overall series (`days`, `form` parameters; `null` rate on days without submissions).
- `GET /v1/admin/stats/daily`: zero-filled daily lead counts per form (`days`, `form`, `spam` parameters).
- `examples/`: a working static HTML landing-page form (shows the `_form_token` flow), a Next.js route
  handler proxy and an n8n webhook signature verification Code node. A test keeps the example form's
  field names in line with the default example schema.

### Changed
- Lead responses include `tags` and `note`. The CSV export has two new columns: `tags` (after
  `status`) and `note` (after `source_url`); scripts that read columns by position need updating.

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
