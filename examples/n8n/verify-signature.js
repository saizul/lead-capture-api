// n8n Code node: verify a Lead Capture API webhook signature.
//
// Setup
//   1. In the Webhook node enable Options -> Raw Body, so the exact signed bytes are available.
//   2. Add this as a Code node (mode "Run Once for All Items") directly after the Webhook node.
//   3. Put the same secret you gave the form's webhook notifier in an n8n credential or environment
//      variable (here: $env.LEADCAPTURE_WEBHOOK_SECRET). Allow `crypto` with
//      NODE_FUNCTION_ALLOW_BUILTIN=crypto on self-hosted n8n.
//
// The signature is "sha256=" + hex HMAC-SHA256 over "<timestamp>.<raw body>".
// Headers: X-LeadCapture-Signature, X-LeadCapture-Timestamp, X-LeadCapture-Delivery, X-LeadCapture-Event.

const crypto = require('crypto');

const secret = $env.LEADCAPTURE_WEBHOOK_SECRET || 'whsec_change_me';
const toleranceSeconds = 300; // reject anything signed more than 5 minutes ago (replay protection)

const item = $input.first();
const headers = item.json.headers || {};
const raw = await this.helpers.getBinaryDataBuffer(0, 'data'); // the untouched request body

const timestamp = headers['x-leadcapture-timestamp'];
const received = headers['x-leadcapture-signature'] || '';

const expected =
  'sha256=' +
  crypto.createHmac('sha256', secret).update(`${timestamp}.`).update(raw).digest('hex');

const validSignature =
  received.length === expected.length &&
  crypto.timingSafeEqual(Buffer.from(received), Buffer.from(expected));
const fresh = Math.abs(Date.now() / 1000 - Number(timestamp)) <= toleranceSeconds;

if (!validSignature || !fresh) {
  throw new Error('Invalid or expired LeadCapture signature');
}

// The delivery id is stable across retries; use it to skip duplicates downstream.
return [
  {
    json: {
      deliveryId: headers['x-leadcapture-delivery'],
      event: headers['x-leadcapture-event'],
      ...JSON.parse(raw.toString('utf8')),
    },
  },
];
