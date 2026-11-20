# Wave 1 (kind, 2026-11-20 15:57–16:12): findings

| Experiment | Runner | What happened | Verdict |
|---|---|---|---|
| api-slow | FAIL | latency page after 6.7 min, resolved 5.3 min after the fault ✓; availability **also** paged: every web → api call timed out (`upstream_requests_total{code="error"}` 100%), so every page was degraded | the alert was right, the hypothesis wrong: 800ms of packet delay on api made web's 2s client timeout fire on every call (amplification along the chain) |
| sample-app-down | FAIL | no page in 15 min; `SLOMetricAbsent` (ticket) after ~10 min; `TargetDown` is a warning that goes nowhere | **blind spot**: with every pod down nothing counts the failed requests, so the worst outage pages nobody. Needs an SLI measured outside the pods |
| api-pod-failure | FAIL | no page in storefront-dev ✓ | passed; failed only because the runner blamed it for api-slow's page in storefront-prod (fixed: each experiment judges its own namespace) |
| api-errors | FAIL | not injected | HTTPChaos cannot run on kind: Docker Desktop's kernel has no ebtables `broute` table (tproxy) |

Platform problems found on the way (all fixed): Chaos Mesh re-synced forever (random certificates / rollme); our own
admission policy denied its `pause:latest`; the daemon kept an old certificate; the runner could not tell a fault that
was never injected and blocked on Chaos Mesh's finalizer; the web SLIs matched no series (route `GET /{$}`), caught by
`SLOMetricAbsent`.
