// Realistic traffic for the SLOs: users have a day (quiet nights, a lunch and an evening peak), mix pages, make
// mistakes (4xx, not counted against an SLO) and sometimes all arrive at once (bursts). A constant 2 req/s would hide
// what matters to SLOs: at night one error is a large share of the traffic.
//
// One compressed day lasts DAY_MINUTES (default 60: one hour = one day); DAYS of them, then the pod restarts.
// Every environment gets the same day, scaled (ENVS="prod:1,staging:0.3,dev:0.3"); PEAK_RPM = prod's peak.
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
const scenarios = {};
for (const [env, scale] of ENVS) {
  const stages = [];
  for (let d = 0; d < DAYS; d++) {
    for (const p of PROFILE) stages.push({ target: Math.max(1, Math.round(PEAK * scale * p)), duration: `${hour * 60}s` });
  }
  scenarios[env] = {
    executor: 'ramping-arrival-rate', exec: 'user', env: { ENV: env },
    startRate: Math.round(PEAK * scale * PROFILE[0]), timeUnit: '1m', stages,
    preAllocatedVUs: 5, maxVUs: 60,
  };
  for (let d = 0; d < DAYS; d++) {
    for (const h of BURST_HOURS) {
      scenarios[`${env}-burst-${d}-${h}`] = {
        executor: 'constant-arrival-rate', exec: 'user', env: { ENV: env },
        rate: Math.round(PEAK * scale * 3), timeUnit: '1m', duration: '60s',
        startTime: `${Math.round((d * DAY + h * hour) * 60)}s`, preAllocatedVUs: 1, maxVUs: 30,
      };
    }
  }
}

export const options = { scenarios, discardResponseBodies: true, summaryTrendStats: ['p(95)'] };

// What users do, with weights; sample-app requests only where it runs.
const MIX = [
  { w: 65, route: 'storefront /', url: (e) => `http://web.storefront-${e}.svc:8080/` },
  { w: 30, route: 'sample-app /hello', hello: true,
    url: (e) => `http://sample-app.sample-app-${e}.svc:8080/hello?name=user${Math.floor(Math.random() * 1000)}` },
  { w: 3, route: 'storefront 404', url: (e) => `http://web.storefront-${e}.svc:8080/products/old-link` },   // stale links
  { w: 2, route: 'sample-app 404', hello: true, url: (e) => `http://sample-app.sample-app-${e}.svc:8080/nope` },
];

export function user() {
  const env = __ENV.ENV;
  const mix = MIX.filter((m) => !m.hello || HELLO_ENVS.includes(env));
  let r = Math.random() * mix.reduce((t, m) => t + m.w, 0);
  const m = mix.find((x) => (r -= x.w) < 0) || mix[0];
  http.get(m.url(env), { timeout: '10s', tags: { route: m.route } });
}
