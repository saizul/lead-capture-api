# Examples

| Folder | What it shows |
| --- | --- |
| [`static-landing/`](static-landing/index.html) | A single-file HTML landing page form. Uses `fetch` to load the signed `_form_token` from `GET /v1/forms/{slug}/schema`, then submits JSON to `POST /v1/forms/{slug}/submit`, with a honeypot, consent checkbox and per-field error display. |
| [`nextjs/`](nextjs/app/api/lead/route.ts) | A Next.js App Router route handler (`/api/lead`) that proxies the schema/token and the submission, forwarding the visitor's IP. |
| [`n8n/`](n8n/verify-signature.js) | A Code node that verifies the `X-LeadCapture-Signature` header of a webhook delivery (HMAC-SHA256 with replay protection). |

## Try the static page

```bash
leadcapture migrate
leadcapture create-form contact "Website contact form" --origin http://127.0.0.1:5500 \
  -f name:text:required:max=120 -f email:email:required -f phone:phone \
  -f "budget:select:options=under-1k|1k-5k|5k-plus" \
  -f message:textarea:required:max=2000 --require-consent
leadcapture serve &
python3 -m http.server 5500 --directory examples/static-landing
```

Open <http://127.0.0.1:5500>. The page talks to `http://127.0.0.1:8000`; change `data-api` and
`data-slug` on the `<form>` element for your deployment, and add the page's origin to the form's
`allowed_origins`.

The field names in `index.html` are checked against the default example form in the test suite
(`tests/test_examples.py`), so the page cannot drift away from the schema it documents.

## Next.js proxy

Copy `nextjs/app/api/lead/route.ts` into your app and set `LEADCAPTURE_URL` and `LEADCAPTURE_FORM`.
Your front end then calls `GET /api/lead` for the form token and `POST /api/lead` to submit, with the same
JSON the static page sends. Run the API with `TRUST_PROXY=true` so rate limits apply per visitor.

## n8n signature check

Paste `n8n/verify-signature.js` into a Code node right after a Webhook node that has *Raw Body*
enabled. It throws unless the signature is valid and the timestamp is less than five minutes old.
