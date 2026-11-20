# backup-normal-writes: FAIL

The negative test of the backup SLOs: dev's database writing all the time (a segment switched and archived every 5 minutes) pages nobody and raises no backup ticket. Before the fix, an idle database paged ("no WAL archived for 15 minutes") although nothing was at risk; with writes the archive must keep up and stay quiet too.

```
16:37:47 [backup-normal-writes] hypothesis: The negative test of the backup SLOs: dev's database writing all the time (a segment switched and archived every 5 minutes) pages nobody and raises no backup ticket. Before the fix, an idle database paged ("no WAL archived for 15 minutes") although nothing was at risk; with writes the archive must keep up and stay quiet too.
16:37:48 [backup-normal-writes] load created in api-factory-dev: Job/experiment-wal-writes
16:43:19 [backup-normal-writes] ✗ unexpected page: ErrorBudgetBurn{namespace=sample-app-dev, slo=sample-app-hello-probe, severity=page, long=6h, short=30m}
17:00:39 [backup-normal-writes] ✗ unexpected page: ErrorBudgetBurn{namespace=storefront-dev, slo=storefront-order-intake, severity=page, long=1h, short=5m}
17:02:55 [backup-normal-writes] load removed
17:02:55 [backup-normal-writes] FAIL
```
