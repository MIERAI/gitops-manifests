// Realistic traffic for the SLOs: users have a day (quiet nights, a lunch and an evening peak), mix pages, make
// mistakes (4xx, not counted against an SLO) and sometimes all arrive at once (bursts). A constant 2 req/s would hide
// what matters to SLOs: at night one error is a large share of the traffic.
//
// One compressed day lasts DAY_MINUTES (default 60: one hour = one day); DAYS of them, then the pod restarts.
// Every environment gets the same day, scaled (ENVS="prod:1,staging:0.3,dev:0.3"); PEAK_RPM = prod's peak.
// ALIGN_CLOCK=1 starts the day at the wall clock's hour (TZ_OFFSET_HOURS, default 9): with DAY_MINUTES=1440 the
// night is the real night, whenever the pod (re)starts. WEB_URL / HELLO_URL: where the services are ({env} = the
// environment; default: in the cluster) — platform/external-users calls the laptop's prod from outside.
import http from 'k6/http';

const DAY = Number(__ENV.DAY_MINUTES || 60);
const DAYS = Number(__ENV.DAYS || 4);
const PEAK = Number(__ENV.PEAK_RPM || 300);
// share of the peak per hour of the day, 0h..23h
const PROFILE = [0.08, 0.05, 0.04, 0.04, 0.05, 0.1, 0.25, 0.45, 0.6, 0.7, 0.75, 0.85,
                 0.95, 0.8, 0.7, 0.7, 0.75, 0.85, 1.0, 0.95, 0.8, 0.55, 0.3, 0.15];
const BURST_HOURS = [12, 20];        // a burst of 3× the peak for one compressed minute at these hours
const ENVS = (__ENV.ENVS || 'prod:1,staging:0.3,dev:0.3').split(',').map((s) => s.split(':'));
const HELLO_ENVS = (__ENV.SAMPLE_APP_ENVS || 'dev,prod').split(',');   // sample-app has no staging

const hour = DAY / 24;               // minutes of one compressed hour
const TZ = Number(__ENV.TZ_OFFSET_HOURS || 9);
// minutes into the day at the start: 0, or (ALIGN_CLOCK) where the wall clock is in its day, scaled to DAY
const OFFSET = __ENV.ALIGN_CLOCK === '1' ? ((((Date.now() / 60000 + TZ * 60) % 1440) + 1440) % 1440) / 1440 * DAY : 0;
const H0 = Math.floor(OFFSET / hour);   // the hour of the day the run starts in
const secs = (min) => `${Math.round(min * 60)}s`;
const scenarios = {};
for (const [env, scale] of ENVS) {
  const stages = [];
  for (let i = 0; i < DAYS * 24; i++) {   // hour by hour from H0; the first one only for what is left of it
    const p = PROFILE[(H0 + i) % 24];
    stages.push({ target: Math.max(1, Math.round(PEAK * scale * p)), duration: `${(i === 0 ? (H0 + 1) * hour - OFFSET : hour) * 60}s` });
  }
  scenarios[env] = {
    executor: 'ramping-arrival-rate', exec: 'user', env: { ENV: env },
    startRate: Math.round(PEAK * scale * PROFILE[H0]), timeUnit: '1m', stages,
    preAllocatedVUs: 5, maxVUs: 60,
  };
  for (let d = 0; d <= DAYS; d++) {
    for (const h of BURST_HOURS) {
      const at = d * DAY + h * hour - OFFSET;   // minutes after the start
      if (at < 0 || at + 1 > DAYS * DAY) continue;
      scenarios[`${env}-burst-${d}-${h}`] = {
        executor: 'constant-arrival-rate', exec: 'user', env: { ENV: env },
        rate: Math.round(PEAK * scale * 3), timeUnit: '1m', duration: '60s',
        startTime: secs(at), preAllocatedVUs: 1, maxVUs: 30,
      };
    }
  }
}

export const options = { scenarios, discardResponseBodies: true, summaryTrendStats: ['p(95)'] };

// What users do, with weights; sample-app requests only where it runs. Orders: ~6.5% of requests (dev peak ≈ 6/min).
const WEB = (e) => (__ENV.WEB_URL || 'http://web.storefront-{env}.svc:8080').replace('{env}', e);
const HELLO = (e) => (__ENV.HELLO_URL || 'http://sample-app.sample-app-{env}.svc:8080').replace('{env}', e);
const MIX = [
  { w: 65, route: 'storefront /', url: (e) => `${WEB(e)}/` },
  { w: 7, route: 'storefront order', order: true, url: (e) => `${WEB(e)}/orders` },
  { w: 30, route: 'sample-app /hello', hello: true,
    url: (e) => `${HELLO(e)}/hello?name=user${Math.floor(Math.random() * 1000)}` },
  { w: 3, route: 'storefront 404', url: (e) => `${WEB(e)}/products/old-link` },   // stale links
  { w: 2, route: 'sample-app 404', hello: true, url: (e) => `${HELLO(e)}/nope` },
];

export function user() {
  const env = __ENV.ENV;
  const mix = MIX.filter((m) => !m.hello || HELLO_ENVS.includes(env));
  let r = Math.random() * mix.reduce((t, m) => t + m.w, 0);
  const m = mix.find((x) => (r -= x.w) < 0) || mix[0];
  if (m.order) {   // a small share of users order; 1 in 20 types a bad amount (a 4xx: not counted against an SLO)
    const amount = Math.random() < 0.05 ? 0 : 100 * (1 + Math.floor(Math.random() * 50));
    http.post(m.url(env), JSON.stringify({ amount }),
      { headers: { 'Content-Type': 'application/json' }, timeout: '10s', tags: { route: m.route } });
    return;
  }
  http.get(m.url(env), { timeout: '10s', tags: { route: m.route } });
}
