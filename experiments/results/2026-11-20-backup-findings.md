# Backups / scheduled jobs (kind, 2026-11-20 16:37–17:50 JST): findings

Expectations were committed before any run (#69). Times are JST in the runner logs, UTC in the queries below.

| Experiment | Runner | In api-factory-dev | Verdict |
|---|---|---|---|
| backup-normal-writes | FAIL | 25 min of writes: 5 segments switched and archived (one per archive_timeout), `ready` max 0, recovery-point SLI 1 throughout; no backup page or ticket ✓ | passed in its own namespace. The FAIL is two pages of other namespaces (sample-app-dev probe SLO, storefront-dev order-intake) from work running in parallel; the runner judges every namespace no experiment of *this* run touches |
| backup-store-unreachable | FAIL | recovery-point fast burn **page after 16.7 min** (≤25 ✓), `PostgresWalArchivingFailing` ticket after 16.9 min (≤25 ✓), base-backup quiet ✓; resolved 5.5 / 10.0 min after the fault (≤15 ✓) | expectations met. FAIL again from neighbours' pages (api-factory-prod rpc-latency, storefront-dev) of parallel experiments. **But** a medium-burn page followed *after* recovery (below) |

Timeline of backup-store-unreachable (UTC): partition 08:03 → first segment `.ready` 08:05 (the writes start with the
fault; archive_timeout switched after ~2 min) → SLI bad 08:15 (10 min of a waiting segment) → fast burn page 08:19:35
→ fault removed 08:19:55 → backlog (4 segments) archived by 08:21 → fast burn resolved 08:25.
Only 3 archive failures in 17 min: with packets dropped, each attempt hangs ~5 min before it fails, so the failure
counter is a slow signal; the waiting-segment SLI is not affected by that.

## Finding: a medium-burn page 11–30 minutes after recovery (young SLO)

`ErrorBudgetBurn{slo=api-factory-recovery-point, long=6h, severity=page}` fired 08:31–08:49, 11 minutes **after** the
archive had caught up. Cause: the SLO was 45 minutes old, so its 6h window held ~1h of samples and the 6 bad
minutes were 11% of it (threshold 7 × 0.5% = 3.5%), and the 30m short window still held them. With a full 6h of
history the same 6 bad minutes are 1.7% and would not page (≈13 bad minutes are needed). Slow-burn tickets (1d/2h,
4d/6h) went pending for the same reason. So: not a wrong rule, but a newly created (or renamed) SLO pages on any
blip for its first hours, and a page that arrives after the problem is gone is unactionable. Options: create SLOs
with absent/warm-up alerts only, or accept and silence for the first 6h. Not changed here.

## Pipeline check of the `age` kind (manual, not an experiment)

A failed nightly job only turns an `age` SLI bad 26 hours after the last success, which a ≤30 min experiment cannot
show. Checked the chain instead: a temporary copy of the base-backup SLO with `max: 10h` (the dev backup was 13.6 h
old) applied in api-factory-dev at 07:37:54 UTC → `slo:age_within_max:bool` = 0 → Pyrra fast burn **ticket fired at
07:41:02** (3.1 min) with labels slo / namespace / severity=ticket → deleted at 07:44.

## The noise that started this

`PostgresWalArchivingStale` (page) fired ~34 of 37.5 h in api-factory-dev and -prod: idle databases write no WAL
(`increase(cnpg_collector_wal_bytes[1h])` = 0), `archive_timeout` only switches a segment with new WAL, nothing was
waiting (`ready` = 0), and a probe write + `pg_switch_wal()` was archived within a second. Removed in #69; since then
no Postgres page in either namespace.

## Not tested

- A base backup or restore check that fails at night → ticket ~2 h later (26 h after the previous success): only the
  `age` → Pyrra → alert chain was checked (above), with a shortened max.
- The off-site copy SLO (laptop only; no experiments there).
- Prod (no faults there); a primary failover during an archive outage; archive_mode switched off (the SLI is blind
  to it: nothing becomes `.ready`).
