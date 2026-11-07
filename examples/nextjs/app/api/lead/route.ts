// Next.js (App Router) route handler that proxies lead submissions to Lead Capture API.
//
// Why proxy? The browser only talks to your own origin, so there is no CORS to configure, the API
// address stays out of your page source, and you can add your own checks (CAPTCHA, logging).
//
// Environment variables (server-side only):
//   LEADCAPTURE_URL    e.g. https://leads.example.com
//   LEADCAPTURE_FORM   form slug, e.g. contact
//
// Run the API with TRUST_PROXY=true so it rate limits by the visitor's address (taken from
// X-Forwarded-For) rather than your server's. Only enable that when this server is the only
// thing that can reach the API.

import { NextRequest, NextResponse } from "next/server";

const BASE = process.env.LEADCAPTURE_URL ?? "http://127.0.0.1:8000";
const FORM = process.env.LEADCAPTURE_FORM ?? "contact";
const FORM_URL = `${BASE.replace(/\/$/, "")}/v1/forms/${encodeURIComponent(FORM)}`;

function visitorIp(req: NextRequest): string | undefined {
  return req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || req.headers.get("x-real-ip") || undefined;
}

function upstreamHeaders(req: NextRequest, json = false): Headers {
  const headers = new Headers();
  if (json) headers.set("Content-Type", "application/json");
  const ip = visitorIp(req);
  if (ip) headers.set("X-Forwarded-For", ip);
  const ua = req.headers.get("user-agent");
  if (ua) headers.set("User-Agent", ua);
  return headers;
}

// GET /api/lead -> field schema plus a fresh signed `form_token`. Call it when the form renders.
export async function GET(req: NextRequest) {
  const res = await fetch(`${FORM_URL}/schema`, { headers: upstreamHeaders(req), cache: "no-store" });
  return NextResponse.json(await res.json(), { status: res.status });
}

// POST /api/lead -> forwards the lead. Send the JSON body you would send to /submit,
// including `_form_token` from the GET above.
export async function POST(req: NextRequest) {
  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return NextResponse.json({ error: { code: "invalid_json", message: "Body is not valid JSON." } }, { status: 400 });
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return NextResponse.json({ error: { code: "invalid_json", message: "Body must be a JSON object." } }, { status: 400 });
  }

  // Record the page the lead came from unless the client already set it.
  const body = { _source_url: req.headers.get("referer") ?? undefined, ...(payload as Record<string, unknown>) };

  let res: Response;
  try {
    res = await fetch(`${FORM_URL}/submit`, {
      method: "POST",
      headers: upstreamHeaders(req, true),
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: { code: "upstream_unreachable", message: "Please try again later." } }, { status: 502 });
  }

  const out = await res.json().catch(() => ({ error: { code: "bad_upstream", message: "Unexpected response." } }));
  const headers = new Headers();
  const retryAfter = res.headers.get("retry-after");
  if (retryAfter) headers.set("Retry-After", retryAfter);
  return NextResponse.json(out, { status: res.status, headers });
}
