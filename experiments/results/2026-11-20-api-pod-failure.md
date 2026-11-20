# api-pod-failure: FAIL

One of the two api pods fails for 10 minutes (crash loop, a lost node). The other one serves; Kubernetes takes the failed pod out of the Service. Nobody should be paged — a page here would be noise.

```
15:57:15 [api-pod-failure] hypothesis: One of the two api pods fails for 10 minutes (crash loop, a lost node). The other one serves; Kubernetes takes the failed pod out of the Service. Nobody should be paged — a page here would be noise.
15:57:16 [api-pod-failure] fault created in storefront-dev: PodChaos/api-one-down
15:57:26 [api-pod-failure] ✓ fault injected (10s)
16:02:13 [api-pod-failure] ✗ unexpected page: ErrorBudgetBurn{namespace=storefront-prod, slo=storefront-web-availability, severity=page, long=1h, short=5m}
16:07:30 [api-pod-failure] fault removed after 10.2 min
16:07:30 [api-pod-failure] FAIL
```
