# Capacity: storefront-staging (2026-11-22 07:56)

**Knee: 320 req/s** (held), first SLO break at 640 req/s: .  
Per pod: **160 req/s** (2 × web, 0.114 cores each at the knee). Recommended ceiling (70% of the knee): **224 req/s**.

**Unstable**: runs at 640 req/s disagreed (held once, broke once) — the result depends on what else ran on the nodes at the time; read the knee as a range, not a number.

What ran out (last good step → first breaking step):

- web: CPU 0.229 → 1.103 cores (2 pods)
- api: CPU 0.133 → 0.560 cores (2 pods)
- nodes: 1.67 → 5.77 of 8 cores busy; outside the containers (host, kubelet, swapping) 0.49 → 1.98
- busiest other namespaces at the break: argocd 0.50, monitoring 0.46, api-factory-prod 0.30

Judged on: web-availability ≥ 99.5%, web-latency ≥ 99%, api-availability ≥ 99.9%, api-latency ≥ 99%, order-intake ≥ 99.5% — each SLI computed over the step minus 30s of warm-up. Load: the app's request mix, open loop, from `F:\k6\k6.exe`.

## Steps

| req/s | verdict | k6 sent/s | k6 p99 ms | web-availability | web-latency | api-availability | api-latency | order-intake | web pods | web CPU | node CPU | host CPU | busiest neighbour |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 80 | holds | 80.0 | 100 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.052 | 12.8% | 0.32 | argocd 0.08 |
| 160 | holds | 159.9 | 92 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.158 | 28.7% | 0.97 | argocd 0.26 |
| 320 | holds | 319.8 | 101 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.229 | 23.9% | 0.49 | monitoring 0.21 |
| 640 | holds | 637.9 | 591 | 1.0000 | 0.9946 | 1.0000 | 1.0000 | 1.0000 | 2 | 1.103 | 74.6% | 1.98 | argocd 0.50 |
| 640 | breaks | 456.5 | 10000 | 1.0000 | 0.9521 | 1.0000 | 0.9997 | — | 0 | 0.250 | 66.3% | 2.44 | argocd 0.72 |
| 1280 | limited | 525.8 | 10000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0 | 0.000 | 54.2% | 2.57 | argocd 0.39 |

## Log

```
07:56:37 [storefront-staging] plan: 80, 160, 320, 640, 1280, 2560 req/s, 90s each; judged on web-availability, web-latency, api-availability, api-latency, order-intake
07:56:42 [storefront-staging] target: web http://100.76.236.114:30662
07:58:38 [storefront-staging]    80/s  holds    k6 80.0/s p99 100ms  web 2 pods CPU 0.052  node 12.8% host 0.32  
08:00:33 [storefront-staging]   160/s  holds    k6 159.9/s p99 92ms  web 2 pods CPU 0.158  node 28.7% host 0.97  
08:02:27 [storefront-staging]   320/s  holds    k6 319.8/s p99 101ms  web 2 pods CPU 0.229  node 23.9% host 0.49  
08:04:22 [storefront-staging]   640/s  holds    k6 637.9/s p99 591ms  web 2 pods CPU 1.103  node 74.6% host 1.98  
08:06:26 [storefront-staging]  1280/s  limited  k6 525.8/s p99 10000ms  web 0 pods CPU 0.000  node 54.2% host 2.57  k6 sent 525.8/s of 1280/s (63059 dropped)
08:06:26 [storefront-staging] confirming 640/s
08:08:30 [storefront-staging]   640/s  breaks   k6 456.5/s p99 10000ms  web 0 pods CPU 0.250  node 66.3% host 2.44  web-latency 0.9521 < 0.9900
08:08:30 [storefront-staging] unstable: runs at 640 req/s disagreed
```
