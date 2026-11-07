// LinkForge load test (DEVOPS_GUIDE §12, ARCHITECTURE §7.1). THREE separate scenarios, never one blended number:
//   redirect_warm   one hot code, constant arrival rate  -> the warm-cache redirect (the benchmark target)
//   redirect_mixed  ~1000 codes (+5% unknown codes)      -> realistic cache hit/miss, negative cache
//   create          link creation at a low controlled rate
// constant-arrival-rate = OPEN model: the request rate is what we say it is, even when the server slows down
// (a closed model with fixed VUs would quietly send fewer requests exactly when the system struggles).
//
// Before running: raise RATE_LIMIT_REDIRECT_PER_MIN and RATE_LIMIT_CREATE_PER_MIN via chart values (the app has
// its own limiter, one k6 machine is ONE client IP, and setup() creates LINKS links), and revert afterwards.
//
// Env: GO_URL (where redirects are fetched), APP_URL (where /api/v1 is called), API_KEY.
//      GO_HOST / APP_HOST: optional Host header overrides (when GO_URL points at a Service instead of the real name).
//      DURATION (default 3m), WARM_RATE (200/s), MIXED_RATE (50/s), CREATE_PER_MIN (20), LINKS (1000).
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const GO = __ENV.GO_URL;
const APP = __ENV.APP_URL;
const KEY = __ENV.API_KEY;
const LINKS = Number(__ENV.LINKS || 1000);
const DURATION = __ENV.DURATION || '3m';
const rateLimited = new Counter('rate_limited_429');

// 302 (redirect), 404 (the unknown codes on purpose) and 201 (create) are all expected outcomes.
http.setResponseCallback(http.expectedStatuses(201, 302, 404));

// ONLY=redirect_warm (or a comma list) runs just those scenarios, so each can be measured on its own: the
// server-side histogram in Prometheus cannot tell scenarios apart, only separate runs can.
const ONLY = (__ENV.ONLY || '').split(',').filter(Boolean);
const wanted = (name) => ONLY.length === 0 || ONLY.includes(name);

const SCENARIOS = {
  redirect_warm: {
    executor: 'constant-arrival-rate', exec: 'warm', duration: DURATION,
    rate: Number(__ENV.WARM_RATE || 200), timeUnit: '1s', preAllocatedVUs: 100, maxVUs: 400,
  },
  redirect_mixed: {
    executor: 'constant-arrival-rate', exec: 'mixed', duration: DURATION,
    rate: Number(__ENV.MIXED_RATE || 50), timeUnit: '1s', preAllocatedVUs: 50, maxVUs: 200,
  },
  create: {
    executor: 'constant-arrival-rate', exec: 'create', duration: DURATION,
    rate: Number(__ENV.CREATE_PER_MIN || 20), timeUnit: '1m', preAllocatedVUs: 5, maxVUs: 10,
  },
};
// CLIENT-side thresholds: they include the network between k6 and the cluster. Server-side numbers come from Prometheus.
const THRESHOLDS = {
  redirect_warm: { 'http_req_duration{scenario:redirect_warm}': ['p(95)<250'], 'checks{scenario:redirect_warm}': ['rate>0.99'] },
  redirect_mixed: { 'http_req_duration{scenario:redirect_mixed}': ['p(95)<250'], 'checks{scenario:redirect_mixed}': ['rate>0.99'] },
  create: { 'checks{scenario:create}': ['rate>0.99'] },
};

export const options = {
  setupTimeout: '300s',
  scenarios: Object.fromEntries(Object.entries(SCENARIOS).filter(([k]) => wanted(k))),
  thresholds: Object.assign(
    { rate_limited_429: ['count==0'] }, // a 429 here means the limiter was NOT raised: the numbers would be wrong
    ...Object.entries(THRESHOLDS).filter(([k]) => wanted(k)).map(([, v]) => v),
  ),
};

function hdr(host, extra) {
  const h = Object.assign({}, extra || {});
  if (host) h.Host = host;
  return h;
}
const goHeaders = () => hdr(__ENV.GO_HOST);
const apiHeaders = () => hdr(__ENV.APP_HOST, { 'X-API-Key': KEY, 'Content-Type': 'application/json' });

function body(tag, i) {
  return JSON.stringify({ destination_url: `https://example.com/k6/${tag}/${i}`, title: `k6 ${tag} ${i}` });
}

export function setup() {
  if (!GO || !APP || !KEY) throw new Error('GO_URL, APP_URL and API_KEY are required');
  const tag = `${Date.now()}`;
  const codes = [];
  for (let start = 0; start < LINKS; start += 50) {
    const reqs = [];
    for (let i = start; i < Math.min(start + 50, LINKS); i++) {
      reqs.push(['POST', `${APP}/api/v1/links`, body(tag, i), { headers: apiHeaders() }]);
    }
    for (const r of http.batch(reqs)) {
      if (r.status !== 201) throw new Error(`setup: create returned ${r.status} (is the create limit raised?)`);
      codes.push(r.json('code'));
    }
  }
  return { codes };
}

export function warm(data) {
  const r = http.get(`${GO}/${data.codes[0]}`, { redirects: 0, headers: goHeaders() });
  if (r.status === 429) rateLimited.add(1);
  check(r, { 'warm is 302': (x) => x.status === 302 });
}

export function mixed(data) {
  const known = Math.random() >= 0.05;
  const code = known ? data.codes[Math.floor(Math.random() * data.codes.length)] : `zz${Math.random().toString(36).slice(2, 8)}`;
  const r = http.get(`${GO}/${code}`, { redirects: 0, headers: goHeaders() });
  if (r.status === 429) rateLimited.add(1);
  check(r, { 'mixed is 302 (known) or 404 (unknown)': (x) => x.status === (known ? 302 : 404) });
}

let n = 0;
export function create() {
  const r = http.post(`${APP}/api/v1/links`, body(`c${__VU}`, `${Date.now()}-${n++}`), { headers: apiHeaders() });
  if (r.status === 429) rateLimited.add(1);
  check(r, { 'create is 201': (x) => x.status === 201 });
}
