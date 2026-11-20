# api-factory-one-pod-down: PASS

One of the two api-factory pods fails for 10 minutes. Kubernetes takes it out of the Service; the other pod serves every RPC and answers the probe. Nobody should be paged. Prediction: no page; at most one failed probe in the seconds before the readiness probe notices (requests that hit the dead pod in that gap are invisible to the in-pod SLIs).

```
16:30:31 [api-factory-one-pod-down] hypothesis: One of the two api-factory pods fails for 10 minutes. Kubernetes takes it out of the Service; the other pod serves every RPC and answers the probe. Nobody should be paged. Prediction: no page; at most one failed probe in the seconds before the readiness probe notices (requests that hit the dead pod in that gap are invisible to the in-pod SLIs).
16:30:31 [api-factory-one-pod-down] fault created in api-factory-prod: PodChaos/api-factory-one-down
16:30:36 [api-factory-one-pod-down] ✓ fault injected (5s)
16:40:39 [api-factory-one-pod-down] fault removed after 10.1 min
16:40:39 [api-factory-one-pod-down] PASS
```
