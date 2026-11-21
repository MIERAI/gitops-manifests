# Capacity results — storefront

| Run | Where from | Knee | Notes |
|---|---|---|---|
| 2026-11-22 07:30 | Mac (runner) + winpc (k6) | 560 req/s held, 640 broke (web-latency) | first runner version; its "what ran out" compared steps run at different times — read the steps table, not that section |
| 2026-11-22 07:56 | winpc (runner + k6) | 320 req/s; 640 unstable | **web OOMKilled at 1280 req/s** (64Mi limit), both pods crash-looping, availability SLI 1.0000 meanwhile: the runner of that time called the step "limited" (fixed since: no ready pod / restarts / k6 failures break a step) |

Both: web had a 5m CPU request (starved by neighbour bursts: Argo CD, k3s, Prometheus) — raised to 100m (#115) after
these runs. Next run: after the change, outside 09:00-17:00.
