# Capacity: storefront-staging (2026-11-22 07:30)

**Knee: 560 req/s** (held), first SLO break at 640 req/s: web-latency 0.9760 < 0.9900.  
Per pod: **280 req/s** (2 × web, 0.640 cores each at the knee). Recommended ceiling (70% of the knee): **392 req/s**.

What ran out (last good step → first breaking step):

- api: CPU 0.548 → 0.425 cores (2 pods)
- web: CPU 1.279 → 0.841 cores (2 pods)
- nodes: CPU 80.7% → 59.7%

Judged on: web-availability ≥ 99.5%, web-latency ≥ 99%, api-availability ≥ 99.9%, api-latency ≥ 99%, order-intake ≥ 99.5% — each SLI computed over the step minus 30s of warm-up. Load: the app's request mix, open loop, from `ssh winpc F:\\k6\\k6.exe`.

## Steps

| req/s | verdict | k6 sent/s | k6 p99 ms | web-availability | web-latency | api-availability | api-latency | order-intake | web pods | web CPU | node CPU |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | holds | 10.0 | 94 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.010 | 11.8% |
| 20 | holds | 20.0 | 120 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.016 | 12.9% |
| 40 | holds | 40.0 | 114 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.032 | 12.6% |
| 80 | holds | 80.0 | 90 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.052 | 13.3% |
| 160 | holds | 159.9 | 85 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.071 | 12.6% |
| 320 | holds | 319.8 | 122 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.241 | 23.6% |
| 400 | holds | 399.6 | 114 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 2 | 0.371 | 32.3% |
| 480 | holds | 479.6 | 338 | 1.0000 | 0.9992 | 1.0000 | 1.0000 | 1.0000 | 2 | 1.049 | 77.2% |
| 560 | holds | 558.9 | 491 | 1.0000 | 0.9923 | 1.0000 | 1.0000 | 1.0000 | 2 | 1.279 | 80.7% |
| 640 | breaks | 636.2 | 884 | 1.0000 | 0.9760 | 1.0000 | 0.9999 | 1.0000 | 2 | 0.841 | 59.7% |

## Log

```
07:30:28 [storefront-staging] plan: 10, 20, 40, 80, 160, 320, 640, 1280, 2560 req/s, 90s each; judged on web-availability, web-latency, api-availability, api-latency, order-intake
07:30:30 [storefront-staging] target: web http://100.76.236.114:32563
07:32:24 [storefront-staging]    10/s  holds    k6 10.0/s p99 94ms  web 2 pods CPU 0.010  node 11.8%  
07:34:18 [storefront-staging]    20/s  holds    k6 20.0/s p99 120ms  web 2 pods CPU 0.016  node 12.9%  
07:36:11 [storefront-staging]    40/s  holds    k6 40.0/s p99 114ms  web 2 pods CPU 0.032  node 12.6%  
07:38:05 [storefront-staging]    80/s  holds    k6 80.0/s p99 90ms  web 2 pods CPU 0.052  node 13.3%  
07:39:59 [storefront-staging]   160/s  holds    k6 159.9/s p99 85ms  web 2 pods CPU 0.071  node 12.6%  
07:41:53 [storefront-staging]   320/s  holds    k6 319.8/s p99 122ms  web 2 pods CPU 0.241  node 23.6%  
07:43:46 [storefront-staging]   640/s  breaks   k6 636.2/s p99 884ms  web 2 pods CPU 0.841  node 59.7%  web-latency 0.9760 < 0.9900
07:43:46 [storefront-staging] breaking step: cooling down 60s, then refining
07:46:40 [storefront-staging]   400/s  holds    k6 399.6/s p99 114ms  web 2 pods CPU 0.371  node 32.3%  
07:48:34 [storefront-staging]   480/s  holds    k6 479.6/s p99 338ms  web 2 pods CPU 1.049  node 77.2%  
07:50:28 [storefront-staging]   560/s  holds    k6 558.9/s p99 491ms  web 2 pods CPU 1.279  node 80.7%  
```
