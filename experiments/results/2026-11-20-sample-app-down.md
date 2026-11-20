# sample-app-down: FAIL

Every sample-app pod fails — users get connection errors on /hello. That is the worst outage there is, so the availability SLO must page within 10 minutes — even though no pod is left to count the failed requests.

```
15:57:15 [sample-app-down] hypothesis: Every sample-app pod fails — users get connection errors on /hello. That is the worst outage there is, so the availability SLO must page within 10 minutes — even though no pod is left to count the failed requests.
15:57:16 [sample-app-down] fault created in sample-app-dev: PodChaos/sample-app-all-down
15:57:26 [sample-app-down] ✓ fault injected (10s)
16:02:13 [sample-app-down] ✗ unexpected page: ErrorBudgetBurn{namespace=storefront-prod, slo=storefront-web-availability, severity=page, long=1h, short=5m}
16:12:30 [sample-app-down] fault removed after 15.2 min
16:12:30 [sample-app-down] ✗ did not fire within 10m: ErrorBudgetBurn {'namespace': 'sample-app-dev', 'slo': 'sample-app-hello-availability', 'severity': 'page'}
16:12:30 [sample-app-down] FAIL
```
