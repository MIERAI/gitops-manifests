# sample-app-down: PASS

Every sample-app pod fails — users get connection errors on /hello. That is the worst outage there is, so the availability SLO must page within 10 minutes — even though no pod is left to count the failed requests.

```
16:27:48 [sample-app-down] hypothesis: Every sample-app pod fails — users get connection errors on /hello. That is the worst outage there is, so the availability SLO must page within 10 minutes — even though no pod is left to count the failed requests.
16:27:48 [sample-app-down] fault created in sample-app-dev: PodChaos/sample-app-all-down
16:27:53 [sample-app-down] ✓ fault injected (5s)
16:30:24 [sample-app-down] ✓ fired after 2.6 min: ErrorBudgetBurn {'namespace': 'sample-app-dev', 'slo': 'sample-app-hello-probe', 'severity': 'page', 'long': '1h'}
16:30:34 [sample-app-down] fault removed after 2.8 min
16:35:51 [sample-app-down] ✓ resolved 5.3 min after the fault: ErrorBudgetBurn {'namespace': 'sample-app-dev', 'slo': 'sample-app-hello-probe', 'severity': 'page', 'long': '1h'}
16:35:51 [sample-app-down] PASS
```

Note: PASS, but 2.6 min is not representative. The probe SLO was ~10 min old, so its "1h" window held 10 min of
data and the ratio rose faster. With a full hour of history a total outage needs ≈4.2 min to cross 7% (14 × 0.5%)
plus `for: 2m` ≈ 6–7 min. Re-run once the SLO has history. (Wave 1, before the probe SLO: no page at all.)
