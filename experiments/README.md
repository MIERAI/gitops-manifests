# Fault experiments

Alerts are tested like code: each file injects one fault and states, **before** it runs, which alerts must fire
within how long, which must stay quiet, and that nothing else may page. `scripts/experiment.py` runs them and writes
a report to `results/`. Principles: gitops-platform-blueprint `docs/slo.md` §5. Faults only on our own clusters.

```sh
# kind (lab): several at once, one per namespace (they share the 1h burn-rate windows)
uv run --with pyyaml python3 scripts/experiment.py --kubeconfig ~/.kube/kind-gitops.yaml \
  experiments/api-errors.yaml experiments/api-slow.yaml experiments/api-pod-failure.yaml experiments/sample-app-down.yaml
```

| Experiment | Fault | Expected |
|---|---|---|
| api-errors | api responses replaced by 503 (HTTPChaos) | web availability pages; web latency quiet |
| api-slow | api packets +800ms (NetworkChaos) | web latency pages; web availability quiet |
| api-pod-failure | one of two api pods fails 10 min | nothing pages |
| api-down | every api pod fails | web availability pages; api "SLI absent" ticket |
| sample-app-down | every sample-app pod fails | sample-app availability pages |
| quiet-traffic | none: 2h of normal traffic | nothing pages (the negative test) |

Format: `hypothesis`, `namespace`, `fault` (Chaos Mesh objects, created in the namespace and always deleted),
`timeout`, `hold` (keep going this long even when everything expected fired), `expect.fire` (`alert`, `labels`,
`within`), `expect.quiet`, `recover` (the fired alerts resolve within this after the fault).

The SLO alerts come from Pyrra: `ErrorBudgetBurn` with labels `slo`, `namespace`, `severity` (page / ticket) and the
windows `long` / `short` (fast burn: `long: 1h`, 14× the budget rate, for 2m); `SLOMetricAbsent` (ticket).
