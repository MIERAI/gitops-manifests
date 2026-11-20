# backup-store-unreachable: FAIL

dev's database keeps writing while its pod cannot reach the object store (partition from the Postgres pod to seaweedfs; the object store itself is untouched). WAL segments pile up unarchived, so the recovery point falls behind: after 10 minutes of a waiting segment the recovery-point SLI turns bad and its fast burn pages; the cause (archive_command failing) is a ticket. The base backup taken last night is still fresh: its SLO stays quiet.

```
17:02:55 [backup-store-unreachable] hypothesis: dev's database keeps writing while its pod cannot reach the object store (partition from the Postgres pod to seaweedfs; the object store itself is untouched). WAL segments pile up unarchived, so the recovery point falls behind: after 10 minutes of a waiting segment the recovery-point SLI turns bad and its fast burn pages; the cause (archive_command failing) is a ticket. The base backup taken last night is still fresh: its SLO stays quiet.
17:02:55 [backup-store-unreachable] load created in api-factory-dev: Job/experiment-wal-writes
17:02:55 [backup-store-unreachable] fault created in api-factory-dev: NetworkChaos/backup-store-unreachable
17:03:00 [backup-store-unreachable] ✓ fault injected (5s)
17:03:46 [backup-store-unreachable] ✗ unexpected page: ErrorBudgetBurn{namespace=api-factory-prod, slo=api-factory-rpc-latency, severity=page, long=1h, short=5m}
17:13:33 [backup-store-unreachable] ✗ unexpected page: ErrorBudgetBurn{namespace=storefront-dev, slo=storefront-order-intake, severity=page, long=6h, short=30m}
17:19:35 [backup-store-unreachable] ✓ fired after 16.7 min: ErrorBudgetBurn {'namespace': 'api-factory-dev', 'slo': 'api-factory-recovery-point', 'severity': 'page', 'long': '1h'}
17:19:50 [backup-store-unreachable] ✓ fired after 16.9 min: PostgresWalArchivingFailing {'namespace': 'api-factory-dev', 'severity': 'ticket'}
17:19:55 [backup-store-unreachable] fault removed after 17.0 min
17:19:55 [backup-store-unreachable] load removed
17:25:27 [backup-store-unreachable] ✓ resolved 5.5 min after the fault: ErrorBudgetBurn {'namespace': 'api-factory-dev', 'slo': 'api-factory-recovery-point', 'severity': 'page', 'long': '1h'}
17:29:58 [backup-store-unreachable] ✓ resolved 10.0 min after the fault: PostgresWalArchivingFailing {'namespace': 'api-factory-dev', 'severity': 'ticket'}
17:29:58 [backup-store-unreachable] FAIL
```
