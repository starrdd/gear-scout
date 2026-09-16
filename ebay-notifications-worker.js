// Cloudflare Worker for eBay's Marketplace Account Deletion notifications.
// Gear Scout does not retain eBay seller identifiers or buyer account data.
const ENDPOINT = 'https://gear-scout-notifications.aaccinelli320.workers.dev/ebay/deletion';
const encoder = new TextEncoder();

function json(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } });
}

function bytesFromBase64(value) {
  return Uint8Array.from(atob(value), c => c.charCodeAt(0));
}

function derToRaw(signature, width) {
  // eBay's Node SDK verifies ASN.1/DER ECDSA signatures; WebCrypto needs r||s.
  let pos = 0;
  if (signature[pos++] !== 0x30) throw new Error('Invalid signature sequence');
  let length = signature[pos++];
  if (length & 0x80) {
    const count = length & 0x7f;
    if (count < 1 || count > 2) throw new Error('Invalid signature length');
    length = 0;
    for (let i = 0; i < count; i++) length = (length << 8) | signature[pos++];
  }
  if (length !== signature.length - pos) throw new Error('Invalid signature length');
  const output = new Uint8Array(width * 2);
  for (let part = 0; part < 2; part++) {
    if (signature[pos++] !== 0x02) throw new Error('Invalid signature integer');
    let size = signature[pos++];
    if (size & 0x80) throw new Error('Invalid signature integer length');
    let bytes = signature.slice(pos, pos + size);
    pos += size;
    while (bytes.length > 1 && bytes[0] === 0) bytes = bytes.slice(1);
    if (bytes.length > width) throw new Error('Signature integer too wide');
    output.set(bytes, part * width + width - bytes.length);
  }
  if (pos !== signature.length) throw new Error('Trailing signature bytes');
  return output;
}

async function appToken(env) {
  const credentials = btoa(`${env.EBAY_CLIENT_ID}:${env.EBAY_CLIENT_SECRET}`);
  const response = await fetch('https://api.ebay.com/identity/v1/oauth2/token', {
    method: 'POST',
    headers: { authorization: `Basic ${credentials}`, 'content-type': 'application/x-www-form-urlencoded' },
    body: 'grant_type=client_credentials&scope=https%3A%2F%2Fapi.ebay.com%2Foauth%2Fapi_scope',
  });
  if (!response.ok) throw new Error(`eBay token HTTP ${response.status}`);
  return (await response.json()).access_token;
}

async function verify(message, signatureHeader, env) {
  if (!signatureHeader) return false;
  const header = JSON.parse(new TextDecoder().decode(bytesFromBase64(signatureHeader)));
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(header.kid || '') || !header.signature) return false;
  const token = await appToken(env);
  const response = await fetch(`https://api.ebay.com/commerce/notification/v1/public_key/${encodeURIComponent(header.kid)}`, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`eBay public key HTTP ${response.status}`);
  const { key: pem } = await response.json();
  const binary = pem.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g, '');
  const key = await crypto.subtle.importKey('spki', bytesFromBase64(binary), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  const signature = derToRaw(bytesFromBase64(header.signature), 32);
  return crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-1' }, key, signature, encoder.encode(JSON.stringify(message)));
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/ebay/deletion') return new Response('Not found', { status: 404 });
    if (request.method === 'GET') {
      const challenge = url.searchParams.get('challenge_code');
      if (!challenge || !env.EBAY_VERIFICATION_TOKEN) return json({ error: 'Missing challenge or token' }, 400);
      const digest = await crypto.subtle.digest('SHA-256', encoder.encode(challenge + env.EBAY_VERIFICATION_TOKEN + ENDPOINT));
      return json({ challengeResponse: Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') });
    }
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });
    if (!env.EBAY_CLIENT_ID || !env.EBAY_CLIENT_SECRET) return json({ error: 'Credentials not configured' }, 503);
    try {
      const message = await request.json();
      if (message?.metadata?.topic !== 'MARKETPLACE_ACCOUNT_DELETION' || !message?.notification?.data) return json({ error: 'Unexpected notification' }, 400);
      if (!(await verify(message, request.headers.get('x-ebay-signature'), env))) return json({ error: 'Invalid signature' }, 412);
      // Gear Scout keeps no seller username, userId, EIAS token, or buyer data.
      // The event was verified and there is no matching personal data to erase.
      return new Response(null, { status: 204 });
    } catch (error) {
      // Do not log payloads or personal identifiers.
      return json({ error: 'Could not verify notification' }, 503);
    }
  },
};
