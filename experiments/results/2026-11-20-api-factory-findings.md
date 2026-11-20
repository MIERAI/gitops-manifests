# api-factory SLOs (kind, 2026-11-20 16:30–18:13): findings

SLOs under test (api-factory/app.yaml): rpc-availability 99.9%, rpc-latency 99% < 100ms, health-probe 99.5%
(/healthz through the Service). Predictions were committed with the experiments (#67) before the runs.

| Experiment | Runner | Predicted | What happened |
|---|---|---|---|
| one-pod-down | PASS | nothing pages; at most one failed probe | nothing paged; 0 failed probes, 0 server errors counted |
| db-slow, run 1 | aborted | — | **fault injected but no effect** (p50 stayed 2.5ms, probe 15ms): the API reaches Postgres through the `postgres-rw` ClusterIP, so its packets never match a filter on the Postgres pod IPs. Corrected by injecting on the Postgres side (its packets go to the API pod IPs); the expectations were not touched |
| db-slow, run 2 | FAIL | rpc-latency page after ~10.5 min | the latency page fired after **15.1 min**, 0.9 min after the fault was removed (the runner's 14m deadline had passed); resolved 4.3 min after the fault. rpc-availability and health-probe quiet ✓ (probe ~0.4s, under the 1s ping timeout) |
| db-partition | PASS | probe page ~6.5 min; rpc-availability blind; SLOMetricAbsent ~12 min; slow recovery | health-probe paged after **6.6 min** ✓, resolved 4.8 min after the fault; rpc-availability silent ✓ (see below); SLOMetricAbsent fired after 6.6 min (not 12); recovery was **not** slow |

## Findings

1. **A full database outage is invisible to the in-pod SLIs** (predicted, confirmed). The readiness probe pings the
   database, so within ~15s every pod left the Service and the synthetic traffic was refused before reaching a pod.
   Only 4 failed RPCs were ever exported (3 `unavailable`, 1 `canceled`) and they count as **zero**: each one created
   a new series whose first value is 1, and `rate()` / `increase()` never count a counter's first value. The
   health probe is what paged. Without it this outage pages nobody.
2. **The liveness probe restarts the API during a database outage.** Liveness uses the same `/healthz` (database
   ping), so each API container was restarted every ~1 min (5 restarts per pod in 6.7 min). Restarts fix nothing
   when the database is gone; they also wipe the counters, which is why `SLOMetricAbsent` (ticket) fired after
   6.6 min. Proposal: liveness on a process-only check (e.g. `tcpSocket` on the http port), readiness may keep the
   database ping. Not changed here. (Predicted wrongly: no restart back-off delayed recovery; pods were ready ~15s
   after the partition was lifted.)
3. **A slow database pages later than the arithmetic says, because the traffic slows down too.** The synthetic
   traffic is a closed loop (4 calls, then sleep 2s): with every RPC ~0.3s it dropped from ~2 to ~1.2 RPC/s, so the
   slow minutes are a smaller share of the 1h window: 14% of the window was crossed after ~13 min, the page fired at
   15.1 min (predicted 10.5). Real clients that wait for an answer behave the same way. With 99% < 100ms, a total
   latency degradation takes ~15 min to page; whether that is acceptable (or the objective / an open-loop traffic
   generator should change) is a decision, not something to tune until the experiment passes.
4. **`AllInjected` does not mean the fault hits the traffic** (db-slow run 1). NetworkChaos with a pod `target`
   filters on pod IPs; traffic through a ClusterIP Service is not matched on the client side. Inject on the
   server side, and check one symptom (latency, probe duration) right after injection.
5. **`canceled` vs `unavailable` is a race**: the synthetic client (`curl --max-time 3`) and the server
   (3s request timeout) give up at the same moment; whichever wins decides whether the failure counts as a server
   error. rpc-latency counts both (whatever the code), so a hanging database still burns that SLO.
6. **The rpc-availability budget on kind prod starts overspent**: on 2026-11-18 ~22:00 all four procedures failed with
   `internal` for ~40 min (1294 of ~264k RPCs in the window, 0.49% against a 0.1% budget). Every 28d window until
   ~2026-12-16 shows the budget exhausted. Kept as history, not excluded.
7. **Runner: pages from other agents' experiments count as unexpected.** During db-slow, pages in storefront-dev
   (`storefront-order-intake`) and before it sample-app-dev (`sample-app-hello-probe`, medium burn) were reported as
   "unexpected page": `running_namespaces` only knows the experiments of the same process. Not caused by this run.

Not tested: a Postgres primary failover (25–49s) staying under the 99.9% fast-burn threshold; an open-loop load
during a slow database; dev (load tests count against dev's SLOs).
