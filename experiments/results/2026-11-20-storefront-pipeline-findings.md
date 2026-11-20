# storefront order pipeline (kind, storefront-dev, 2026-11-20 16:46–20:13 JST): findings

The asynchronous shape: web takes `POST /orders`, two workers claim them (`FOR UPDATE SKIP LOCKED`), web measures
the pipeline from outside (watermark, overdue orders). SLOs: order-intake (99.5%), order-freshness (every order
older than 60s processed, 99.5% of the time; `age` type), order-completeness (processed within 5 min, 99.9%; `ratio`
type). Predictions were committed before the runs (#71). Dev traffic follows the compressed day (k6 restarted 16:41,
one day per hour): night = hours 0–6 of each hour, ~0.3 orders/min; day ~4–7 orders/min.

| Experiment | Runner | What happened (vs the prediction) | Verdict |
|---|---|---|---|
| worker-one-down (night, 16:47) | PASS | nothing paged; max freshness lag 9s; only 3 orders in the 10 min (night) | held, but a weak test: rerun by day |
| worker-one-down (day, 20:03) | PASS | nothing paged; 56 orders in the 10 min, max freshness lag 14s | prediction held |
| db-unreachable | FAIL | intake page after **3.3 min** (predicted ~7); freshness "SLI absent" ticket after 8.4 min ✓; freshness / completeness / page SLOs quiet ✓; both resolved 2.5 / 4.5 min after the fault. FAIL only for a page in api-factory-prod (another experiment running at the same time, outside this run) | expectations held; the timing prediction was wrong: it assumed constant traffic, but the hour before was the compressed night, so 3 min of errors were already 7% of the hour's orders |
| worker-slow (+2s) | FAIL | the delay worked (orders took 8.1s, predicted ~8s); max freshness lag 28s; freshness and completeness quiet ✓. FAIL for intake's **medium-burn page** left over from db-unreachable (below) and a page in api-factory-dev (another experiment) | hypothesis held; the run was contaminated by the previous experiment |
| worker-down | FAIL | freshness page after **8.1 min**, completeness page after **8.4 min** (predicted ~8 each) ✓; intake quiet ✓; resolved 5.0 / 4.8 min after the fault; 8 orders counted overdue. FAIL only for a page in api-factory-prod (another experiment) | expectations held, timings as predicted |
| worker-overloaded (+8s) | PASS | by day (~7 orders/min): backlog 8 → 15, freshness page after 7.7 min ✓; no order reached 5 min: completeness quiet ✓; resolved 5.6 min after | prediction held |

## Findings

1. **Every short outage pages twice.** After each of the four fast-burn pages the medium-burn page (6h / 30m
   windows, 7×, for 15m) fired 8–20 min *after* recovery and stayed ~20 min: intake 17:13–17:33 (outage
   16:57–17:05), freshness 18:21–18:40 and completeness 18:23–18:39 (outage 18:02–18:10), freshness again ~19:40
   (outage 19:12–19:19). Part of this is an artifact of new SLIs — the 6h window held only 0.5–3h of data (first
   samples 16:45), so 8 min was 5–25% of it instead of 2.4% — and part is dev's low traffic (event-based SLIs: an outage at the
   end of the night is a large share of the 6h events). Steady state should be checked once the SLIs have 6h of
   history; if the second page persists, it is noise (a page for an incident already over) and the medium burn
   should be a ticket for dev, or need a longer outage.
2. **NetworkChaos `target` does not see Service traffic from the client side.** Selected on the workers with target
   Postgres, +2s left the order delay at 0.45s (no effect); selected on Postgres with target the workers, +2s gave
   8.1s. The client sends to the Service IP (translated on the node), the target is a set of pod IPs. Experiments
   whose fault is "client → database, direction to" may inject nothing while Chaos Mesh reports AllInjected
   (api-factory-db-slow is written that way; `partition` with `direction: both` also applies on the database side).
3. **The pipeline SLIs survive the loss of every worker** because web measures them: with all workers down the
   worker's own metrics vanished, freshness and completeness still paged in ~8 min.
4. **Database unreachable: intake pages, freshness goes absent (ticket).** By design (unknown is not fresh), but
   it means the freshness page cannot be relied on for a database outage; intake carries it.
5. **The runner judges pages in namespaces outside its run.** Three of five FAILs are pages of other agents'
   experiments (api-factory-prod / -dev) running at the same time. A shared lab needs either a lock or the runner
   judging only its own namespace when others are known to be busy.

## Not tested / open

- Correctness (amount unchanged): no SLI.
- A poison order (one order whose work always fails): freshness would page on a single stuck order; not run.
- staging / prod still run the old web and worker images (not promoted): their new SLOs show "SLI absent" tickets,
  their worker targets are down until a promotion.
- Night-time variants (worker-one-down by day; worker-down at night, when the first order after the failure may
  take minutes to arrive).
