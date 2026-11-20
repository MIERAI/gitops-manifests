# api-errors: FAIL

Every api response to GET /products is replaced by a 503 on its way out (a broken proxy / sidecar between web and api). Users get pages without products; web's availability SLO must page. api's own SLO does not see it (api answered 200) — the page is on web, and web's upstream_requests_total{code="503"} names api as the cause.

```
15:57:15 [api-errors] hypothesis: Every api response to GET /products is replaced by a 503 on its way out (a broken proxy / sidecar between web and api). Users get pages without products; web's availability SLO must page. api's own SLO does not see it (api answered 200) — the page is on web, and web's upstream_requests_total{code="503"} names api as the cause.
15:57:16 [api-errors] fault created in storefront-staging: HTTPChaos/api-503
16:01:42 [api-errors] fault removed after 4.4 min
16:01:42 [api-errors] ✗ runner error: fault not injected after 90s: HTTPChaos/api-503 (failed to apply for pod storefront-staging/api-6bd4f84d77-6zwvn: rpc error: code = Unknown desc = create http chaos: execute command(/usr/local/bin/nsexec -l -n /proc/234962/ns/net -p /proc/234962/ns/pid -- /usr/local/bin/tproxy -i -vv): process with identifier tproxy-containerd://54b34ebae9ea575e98431d7ac0dd87b94ea0f13760c35567a35b97fe7be466a2 is running)
16:01:42 [api-errors] FAIL
```
