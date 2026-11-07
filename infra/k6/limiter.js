// A SMALL, SEPARATE test of the rate limiter itself (DEVOPS_GUIDE §12 trap 1): run it with the DEFAULT limits
// (120 redirects/min per client IP, 30 creates/min per user), i.e. AFTER the load-test limits were reverted.
//   redirect_limit  150 redirects as fast as possible, each with a DIFFERENT spoofed X-Forwarded-For. The limiter
//                   must key on the real client IP (the proxy-added address), so the spoof must buy nothing:
//                   about 120 are 302, the rest 429.
//   create_limit    40 creates with the API key: about 30 are 201, the rest 429 (limit is per USER).
// Limits are FIXED one-minute windows: a run that straddles a window boundary can let up to twice as many through,
// so the pass range is deliberately a little wide; re-run if it lands outside once.
//
// Env: GO_URL, APP_URL, API_KEY, GO_HOST / APP_HOST (optional Host overrides), as for load.js.
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const GO = __ENV.GO_URL;
const APP = __ENV.APP_URL;
const KEY = __ENV.API_KEY;
const redirect302 = new Counter('redirect_302');
const redirect429 = new Counter('redirect_429');
const create201 = new Counter('create_201');
const create429 = new Counter('create_429');
http.setResponseCallback(http.expectedStatuses(201, 302, 429));

export const options = {
  setupTimeout: '60s',
  scenarios: {
    redirect_limit: { executor: 'shared-iterations', exec: 'redirects', vus: 10, iterations: 150, maxDuration: '30s' },
    create_limit: { executor: 'shared-iterations', exec: 'creates', vus: 4, iterations: 40, maxDuration: '30s', startTime: '35s' },
  },
  thresholds: {
    redirect_302: ['count>=100', 'count<=140'],
    redirect_429: ['count>=10'],
    create_201: ['count>=20', 'count<=35'],
    create_429: ['count>=5'],
  },
};

const hdr = (host, extra) => Object.assign({}, extra || {}, host ? { Host: host } : {});

export function setup() {
  const r = http.post(`${APP}/api/v1/links`, JSON.stringify({ destination_url: 'https://example.com/k6/limiter' }),
    { headers: hdr(__ENV.APP_HOST, { 'X-API-Key': KEY, 'Content-Type': 'application/json' }) });
  if (r.status !== 201) throw new Error(`setup create returned ${r.status}`);
  return { code: r.json('code') };
}

export function redirects(d) {
  const spoof = `${1 + Math.floor(Math.random() * 222)}.${Math.floor(Math.random() * 255)}.${Math.floor(Math.random() * 255)}.${1 + Math.floor(Math.random() * 253)}`;
  const r = http.get(`${GO}/${d.code}`, { redirects: 0, headers: hdr(__ENV.GO_HOST, { 'X-Forwarded-For': spoof }) });
  if (r.status === 302) redirect302.add(1);
  if (r.status === 429) redirect429.add(1);
  check(r, { 'redirect is 302 or 429': (x) => x.status === 302 || x.status === 429 });
}

export function creates() {
  const r = http.post(`${APP}/api/v1/links`, JSON.stringify({ destination_url: `https://example.com/k6/limiter/${Date.now()}-${Math.random()}` }),
    { headers: hdr(__ENV.APP_HOST, { 'X-API-Key': KEY, 'Content-Type': 'application/json' }) });
  if (r.status === 201) create201.add(1);
  if (r.status === 429) create429.add(1);
  check(r, { 'create is 201 or 429': (x) => x.status === 201 || x.status === 429 });
}
