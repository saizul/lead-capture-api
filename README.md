# Lead Capture API

A production-style lead intake service for websites and landing pages. Point any HTML form (or a
no-code builder, or your own front end) at it and get validated, spam-filtered leads in your database
and in your inbox, Slack, Telegram or automation tool, with retries when a destination is down.

Built with FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 and httpx. Runs on SQLite out of the box
and PostgreSQL in production.

[![CI](https://github.com/saizulamin/lead-capture-api/actions/workflows/ci.yml/badge.svg)](https://github.com/saizulamin/lead-capture-api/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12%20|%203.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Flexible intake**: `POST /v1/forms/{slug}/submit` takes JSON, URL-encoded or multipart bodies.
- **Per-form schemas in the database**: field types (text, textarea, email, phone, url, number, integer,
  boolean, select, date), required flags, min/max length, numeric ranges, select options.
- **Bot defences**: honeypot field, signed form token with a minimum time-to-submit, spam scoring
  (links, blocked words, disposable email domains), per-IP and per-form rate limits
  (in-memory, or Redis for multi-worker deployments), per-form CORS allow-list.
- **Privacy**: consent flag with timestamp, IPs stored only as salted HMAC hashes, GDPR-style erasure
  endpoint and a retention CLI command.
- **Reliable delivery**: transactional outbox with exponential backoff and jitter, stale-lock recovery,
  full delivery logs and manual retry. Notifiers: HMAC-signed webhook (n8n, Zapier, Make), SMTP email,
  Telegram bot, Slack incoming webhook.
- **Admin API** (hashed API keys): form CRUD, lead search and filters, status tracking
  (`new` / `contacted` / `won` / `lost`), tags and internal notes with bulk updates, CSV export,
  delivery logs.
- **Operations**: structured JSON logs with request IDs, `/healthz` and `/readyz`, OpenAPI docs with
  examples, Dockerfile, docker-compose (API + PostgreSQL + Redis), Makefile, CI.

## Architecture

```mermaid
flowchart LR
    V[Visitor browser] -->|GET schema + token| API
    V -->|POST submit| API
    subgraph API[Lead Capture API - FastAPI]
        direction TB
        CORS[CORS allow-list] --> RL[Rate limiter<br/>memory or Redis]
        RL --> HP[Honeypot + form token<br/>time-to-submit]
        HP --> VAL[Field validation<br/>per-form schema]
        VAL --> SP[Spam scoring]
        SP --> STORE[(Leads + outbox<br/>one transaction)]
    end
    STORE -->|background task| DISP[Outbox dispatcher<br/>retry + backoff]
    WORKER[Poll worker] --> DISP
    DISP --> WH[Webhook<br/>HMAC signed]
    DISP --> EM[SMTP email]
    DISP --> TG[Telegram]
    DISP --> SL[Slack]
    DISP --> LOG[(Delivery logs)]
    ADMIN[Admin / dashboard / scripts] -->|X-API-Key| ADM[Admin API]
    ADM --> STORE
    ADM --> LOG
    DB[(SQLite or PostgreSQL)] --- STORE
    DB --- LOG
```

A submission is accepted, validated and stored together with one outbox row per enabled notifier in a
single transaction. A background task attempts delivery immediately; failures are rescheduled with
exponential backoff (`30s, 60s, 120s, ...` capped at one hour, with jitter) by a polling worker until
the attempt limit is reached, then marked `dead` (retry manually via the admin API). A slow or broken
integration never delays or fails the visitor's request.

## Quick start

```bash
git clone https://github.com/saizulamin/lead-capture-api.git
cd lead-capture-api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # add ,postgres or ,redis for those backends

cp .env.example .env               # edit secrets
leadcapture migrate                # creates leadcapture.db (SQLite)
leadcapture create-admin-key me    # prints the key once; store it
leadcapture create-form contact "Website contact form" \
  --origin https://example.com --webhook-url https://n8n.example.com/webhook/leads \
  --webhook-secret whsec_change_me --require-consent
leadcapture serve                  # http://127.0.0.1:8000/docs
```

With Docker (API + PostgreSQL + Redis):

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec api leadcapture create-admin-key me
docker compose exec api leadcapture create-form contact "Contact" --origin https://example.com
```

### CLI

| Command | Purpose |
| --- | --- |
| `leadcapture migrate [revision]` | Apply Alembic migrations (default `head`). |
| `leadcapture create-admin-key NAME` | Create an admin API key (shown once, stored hashed). |
| `leadcapture create-form SLUG NAME` | Create a form. `-f name:type[:required][:max=N]` is repeatable; `--fields-file` takes JSON. |
| `leadcapture list-forms` | List forms. |
| `leadcapture purge-leads --older-than-days N` | Retention: delete old leads (cascades to outbox and logs). |
| `leadcapture serve` | Run uvicorn. In production run `uvicorn leadcapture.main:create_app --factory`. |

## Configuration

All settings are environment variables (or `.env`). See [`.env.example`](.env.example).

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite+aiosqlite:///./leadcapture.db` | `postgresql+asyncpg://user:pass@host/db` for PostgreSQL. `postgres://` and `postgresql://` are normalised. |
| `SECRET_KEY` | placeholder | Signs form tokens. Must be changed when `ENVIRONMENT=production` (the app refuses to start otherwise). |
| `IP_HASH_SALT` | placeholder | Salt for IP hashing. Same production rule. Changing it breaks hash continuity. |
| `TRUST_PROXY` | `false` | Read the client IP from `X-Forwarded-For`. Enable only behind a proxy you control. |
| `REDIS_URL` | unset | Enables the shared rate limiter (`pip install ".[redis]"`). |
| `OUTBOX_MAX_ATTEMPTS` | `6` | Attempts before an event is marked `dead`. |
| `OUTBOX_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | `30` / `3600` | Backoff is `base * 2^(attempt-1)`, capped, +/-10% jitter. |
| `OUTBOX_WORKER_ENABLED` | `true` | Background poller for retries. Safe to run in several processes (rows are claimed atomically). |
| `SMTP_*` | unset | Needed for the email notifier. |
| `TELEGRAM_BOT_TOKEN` | unset | Default bot token; a notifier can carry its own. |

## Using the API

Interactive docs are served at `/docs` (Swagger UI) and `/redoc`. Admin calls send
`X-API-Key: <key>` (or `Authorization: Bearer <key>`).

### Create a form

```bash
curl -X POST http://localhost:8000/v1/admin/forms \
  -H "X-API-Key: $LC_KEY" -H "Content-Type: application/json" \
  -d '{
    "slug": "contact",
    "name": "Website contact form",
    "fields": [
      {"name": "name", "type": "text", "required": true, "max_length": 120},
      {"name": "email", "type": "email", "required": true},
      {"name": "budget", "type": "select", "options": ["under-1k", "1k-5k", "5k-plus"]},
      {"name": "message", "type": "textarea", "required": true, "max_length": 2000}
    ],
    "allowed_origins": ["https://example.com"],
    "require_consent": true,
    "min_submit_seconds": 3,
    "notifiers": [
      {"type": "webhook", "url": "https://n8n.example.com/webhook/leads", "secret": "whsec_change_me"},
      {"type": "slack", "webhook_url": "https://hooks.slack.com/services/T000/B000/XXXX"},
      {"type": "telegram", "chat_id": "-1001234567890"},
      {"type": "email", "to": ["sales@example.com"]}
    ]
  }'
```

Notifier secrets are returned masked (`***`). Send `***` back in a `PATCH` to keep the stored value.

### Submit a lead

```bash
# JSON (server-to-server: no Origin header, no form token needed when min_submit_seconds = 0)
curl -X POST http://localhost:8000/v1/forms/contact/submit \
  -H "Content-Type: application/json" \
  -d '{"name":"Ada Lovelace","email":"ada@example.com","message":"Need a landing page","consent":true}'

# Form-encoded
curl -X POST http://localhost:8000/v1/forms/contact/submit \
  -d "name=Ada&email=ada@example.com&message=Hello&consent=on"
```

Responses: `201 {"id": "...", "status": "received"}`; `422` with per-field errors
(`{"error": {"code": "validation_failed", "fields": {"email": "Enter a valid email address."}}}`);
`429` with `Retry-After`; `403` for a disallowed origin.

Honeypot hits and spam-scored submissions also return `201`, so bots learn nothing. Spam is stored with
`is_spam = true` for review but never triggers notifications.

### Search, update, export

```bash
curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/leads?status=new&q=acme&limit=20"
curl -X PATCH -H "X-API-Key: $LC_KEY" -H "Content-Type: application/json" \
  -d '{"status":"contacted"}' http://localhost:8000/v1/admin/leads/<lead-id>
curl -H "X-API-Key: $LC_KEY" -o leads.csv "http://localhost:8000/v1/admin/leads/export.csv?form=contact"
curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/deliveries?success=false"
curl -X POST -H "X-API-Key: $LC_KEY" http://localhost:8000/v1/admin/outbox/<event-id>/retry
curl -X DELETE -H "X-API-Key: $LC_KEY" http://localhost:8000/v1/admin/leads/<lead-id>   # erasure
```

Spam is hidden from lead lists by default; use `spam=include` or `spam=only`.

### Tags and notes

Leads carry free-form tags (for pipelines, campaigns or owners) and one internal note. `PATCH`
accepts any mix of `status`, `tags` (replace the list), `add_tags` / `remove_tags`, and `note`;
omitted fields are left as they are.

```bash
curl -X PATCH -H "X-API-Key: $LC_KEY" -H "Content-Type: application/json" \
  -d '{"status":"contacted","add_tags":["vip","campaign:spring"],"note":"Called, quote on Friday."}' \
  http://localhost:8000/v1/admin/leads/<lead-id>

# same status / tag change for up to 500 leads in one request (all-or-nothing)
curl -X POST -H "X-API-Key: $LC_KEY" -H "Content-Type: application/json" \
  -d '{"ids":["<id-1>","<id-2>"],"add_tags":["campaign:spring"],"remove_tags":["cold"]}' \
  http://localhost:8000/v1/admin/leads/bulk-update
# {"updated":2,"not_found":[]}

curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/leads?tag=vip&tag=campaign:spring"
curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/tags?form=contact"
# [{"tag":"vip","count":12},{"tag":"campaign:spring","count":4}]
```

- Tags are lower-cased and de-duplicated; 1-32 characters from `a-z 0-9 - _ . :`, starting with a
  letter or digit; at most 20 per lead (`422 too_many_tags` otherwise).
- `tag` filters are exact and repeatable (a lead must carry every tag given). They work on the lead
  list, the CSV export and `GET /v1/admin/tags`, together with the other lead filters.
- `note` is capped at 5000 characters; send `null` or `""` to clear it. Notes are admin-only and are
  never sent to notifiers.
- The CSV export gains a `tags` column (`;`-separated) after `status` and a `note` column after
  `source_url`.

Lead volume over time (zero-filled UTC days, one count array per form, handy for charts):

```bash
curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/stats/daily?days=14&form=contact"
# {"days":14,"spam":"exclude","dates":["2026-01-01",...],"forms":{"contact":[0,2,...]},"total":[0,2,...]}
```

`days` is 1-366 (default 30); `form` limits the series to one slug (404 if unknown); `spam` works as for lead lists.

Spam rate per form and day (useful for tuning a form's spam threshold or spotting an attack):

```bash
curl -H "X-API-Key: $LC_KEY" "http://localhost:8000/v1/admin/stats/spam-rate?days=14&form=contact"
# {"days":14,"dates":[...],"forms":{"contact":{"total":[0,4,...],"spam":[0,1,...],"rate":[null,0.25,...],"overall_rate":0.25}},"overall":{...}}
```

`rate` is `spam / total` for the day (0-1) and `null` on days without submissions, so charts can show a gap instead of 0%. `overall` aggregates every form in the window; `days` and `form` work as above.

### HTML form snippet

The form fetches a signed token when it renders (this powers the minimum time-to-submit check) and
includes a honeypot input that humans never see.

```html
<form id="lead-form">
  <input name="name" placeholder="Your name" required maxlength="120">
  <input name="email" type="email" placeholder="Email" required>
  <textarea name="message" placeholder="How can we help?" required></textarea>

  <!-- honeypot: hidden from people, irresistible to bots -->
  <div style="position:absolute;left:-9999px" aria-hidden="true">
    <input name="website_url" tabindex="-1" autocomplete="off">
  </div>

  <label><input type="checkbox" name="consent"> I agree to be contacted about my enquiry.</label>
  <input type="hidden" name="_form_token">
  <button type="submit">Send</button>
  <p id="lead-status" role="status"></p>
</form>

<script>
  const API = "https://leads.example.com";
  const form = document.getElementById("lead-form");
  const status = document.getElementById("lead-status");

  fetch(`${API}/v1/forms/contact/schema`)
    .then((r) => r.json())
    .then((s) => (form._form_token.value = s.form_token));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(form));
    body.consent = form.consent.checked;
    const res = await fetch(`${API}/v1/forms/contact/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) {
      form.reset();
      status.textContent = "Thanks, we will be in touch.";
    } else {
      const { error } = await res.json();
      status.textContent = Object.values(error.fields || { _: error.message }).join(" ");
    }
  });
</script>
```

Remember to add your site's origin to the form's `allowed_origins`. A plain (no JavaScript) HTML
`<form method="post" action=".../submit">` also works: the browser sends URL-encoded data and, if the
form has a `success_redirect_url`, receives a `303` redirect to your thank-you page.

More complete, copy-paste examples live in [`examples/`](examples/README.md): a static HTML landing
page, a Next.js route handler that proxies submissions, and an n8n signature-check Code node.

## Webhook payload and signature verification

Every webhook delivery is a JSON `POST` with these headers:

| Header | Meaning |
| --- | --- |
| `X-LeadCapture-Event` | `lead.created` |
| `X-LeadCapture-Delivery` | Outbox event ID (stable across retries, use it to deduplicate) |
| `X-LeadCapture-Timestamp` | Unix seconds when the attempt was signed |
| `X-LeadCapture-Signature` | `sha256=` + hex HMAC-SHA256 of `"<timestamp>.<raw body>"` with your secret |

Reject requests whose timestamp is more than a few minutes old to stop replays.

### n8n (Webhook node + Code node)

In the **Webhook** node enable *Options -> Raw Body*, then add a **Code** node (Run Once for All Items)
right after it:

```javascript
const crypto = require('crypto');

const secret = 'whsec_change_me';
const headers = $input.first().json.headers;
const raw = await this.helpers.getBinaryDataBuffer(0, 'data'); // exact bytes that were signed

const timestamp = headers['x-leadcapture-timestamp'];
const expected = 'sha256=' + crypto
  .createHmac('sha256', secret)
  .update(`${timestamp}.`)
  .update(raw)
  .digest('hex');

const received = headers['x-leadcapture-signature'] || '';
const valid =
  received.length === expected.length &&
  crypto.timingSafeEqual(Buffer.from(received), Buffer.from(expected));
const fresh = Math.abs(Date.now() / 1000 - Number(timestamp)) < 300;

if (!valid || !fresh) {
  throw new Error('Invalid LeadCapture signature');
}
return [{ json: JSON.parse(raw.toString('utf8')) }];
```

### Python (any framework)

```python
from leadcapture.security import verify_signature  # or copy the 6-line function

ok = verify_signature(
    secret="whsec_change_me",
    timestamp=int(request.headers["X-LeadCapture-Timestamp"]),
    body=raw_body_bytes,
    signature=request.headers["X-LeadCapture-Signature"],
    tolerance=300,
)
```

## Spam scoring

Scores are additive and explainable (stored in `spam_reasons`). A lead at or above the form's
`spam_threshold` (default 50) is flagged and silently dropped from notifications.

| Signal | Points |
| --- | --- |
| 1 / 2 / 3+ links in text fields | 10 / 25 / 50 |
| Blocked word or phrase (built-in list plus per-form `blocked_words`) | 30 each, max 90 |
| Disposable email domain | 40 |

Fields of type `url` do not count as links, so a legitimate "your website" field is fine.

## Security notes

- **Admin keys** are 256-bit random values, shown once, and stored as SHA-256 hashes. Revoke by setting
  `is_active = false` in the `api_keys` table. Run the API behind HTTPS.
- **IP addresses** are never stored in plain text: leads keep `HMAC-SHA256(IP_HASH_SALT, ip)`, and rate
  limit keys use the same hash (so Redis never sees raw IPs). Keep the salt secret and stable.
- **Webhook URLs are admin-supplied.** The API makes outbound requests to them, so only give admin keys
  to people you trust, and run the service where it cannot reach internal-only endpoints you care about.
  Webhook and Slack URLs must be `http(s)` / `https`.
- **CORS** is enforced per form: a request carrying an `Origin` header that is not on the allow-list is
  rejected with `403` (this also covers cross-site plain HTML form posts). Requests without an `Origin`
  header (servers, curl) are allowed; protect those with rate limits and the honeypot/token checks.
- **Rate limits** are per form+IP and per form. The in-memory limiter is per process; use `REDIS_URL`
  with more than one worker. The Redis limiter fails open if Redis is down. Set `TRUST_PROXY=true`
  only behind a proxy that overwrites `X-Forwarded-For`.
- **CSV export** prefixes cells starting with `= + - @` to prevent spreadsheet formula injection.
- **Secrets in notifier config** (webhook secret, bot tokens, Slack URLs) live in the database in
  plain text, as the service must send them. They are masked in every API response. Use disk/database
  encryption and restrict database access.
- **Production guard**: with `ENVIRONMENT=production` the app refuses to start on default secrets.
- This is a spam *reduction* layer, not a CAPTCHA. For hostile traffic, put a WAF or Turnstile/hCaptcha in front.

## Development

```bash
make install     # venv + dev dependencies
make test        # pytest
make lint        # ruff check + format check
make typecheck   # mypy
```

The suite (pytest, pytest-asyncio, httpx `AsyncClient`, respx) covers validation, spam rules, rate
limiting, notifier retries, the outbox, admin auth, CSV export, the CLI and migrations. Tests use
temporary SQLite databases and mock all outbound HTTP.

Project layout:

```
leadcapture/
  main.py            app factory, middleware, lifespan
  routers/           public.py, admin.py, health.py
  services/          spam.py, ratelimit.py, outbox.py, leads.py
  notifiers/         webhook.py, email.py, telegram.py, slack.py, configs.py
  fields.py          field definitions and validation
  models.py db.py    SQLAlchemy models, engine helpers
  migrations/        Alembic environment and versions (run `leadcapture migrate` after upgrading)
  cli.py             Typer CLI
tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE). Created by [Saizul Amin](https://saizul.com).

## Container image

A multi-arch image (amd64, arm64) is published to GitHub Container Registry on every change to `main`:

```bash
docker pull ghcr.io/saizul/lead-capture-api:latest
docker run -p 8000:8000 -v leads:/data ghcr.io/saizul/lead-capture-api:latest
```
