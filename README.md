# gitops-manifests

> Part of the [GitOps Platform Blueprint](https://github.com/MIERAI/gitops-platform-blueprint)

The desired state of every workload, per environment. Argo CD (installed by
[infrastructure-live](https://github.com/MIERAI/infrastructure-live)) watches this repository and keeps the cluster in sync.
**Changing the cluster = committing here.**

## Run it yourself

A local cluster that syncs this repository, without our keys or tokens: infrastructure-live
[`tofu/demo`](https://github.com/MIERAI/infrastructure-live/tree/main/tofu/demo) (one command; the platform and every
public application, `clusters/demo`). To change things, fork this repository, run `scripts/fork.sh <your owner>`,
commit, and build the demo cluster from your fork.

## Add an application: one directory

```
<app>/app.yaml             the one place it is registered (example: storefront/app.yaml)
<app>/<kustomize dir>/<env>/        a kustomize component, and/or
<app>/<helm dir>/chart + values-<env>.yaml   a Helm component
```

Everything else follows from `app.yaml`: the Argo CD Applications (`apps/applications.yaml`, an ApplicationSet: one per
component × environment, namespace `<name>-<env>`), the namespace labels (admission policies, image signatures when
`signedImages`, the GHCR pull secret cloned by Kyverno), which file a promotion edits (`scripts/promote.sh`,
`set-image.sh`), CI rendering (`scripts/render.py`) and the delivery board's pipelines. **Remove an application =
remove its directory** (the workloads stay until their namespaces are deleted: the ApplicationSet preserves them).
Every application is registered this way: api-factory, sample-app, storefront.

## Layout

```
apps/                      Argo CD Applications (app of apps): applications.yaml (every */app.yaml) + the platform
clusters/demo|laptop/      per cluster: demo = apps/ without private applications; laptop = apps/ + its extras
api-factory/base/          Postgres (local only) → API (init container: goose migrations) → smoke Job (PostSync hook), ordered by sync waves
api-factory/overlays/      dev / prod: namespace, replicas, NodePort, image tags
sample-app/base|overlays/  the public reference app
storefront/                a monorepo app registered by app.yaml: web + api (kustomize), worker (Helm), dev → staging → prod
scripts/set-image.sh       used by application CI (via the shared promote-dev workflow) to update a tag in dev
scripts/promote.sh         moves a version that ran in one environment to the next (soak, one image or all; promote workflow)
scripts/render.py          renders every Application as Argo CD does (kustomize / Helm, incl. app.yaml ones)
scripts/validate.sh        render + kubeconform + admission policies for every application (CI: validate)
scripts/alert-report.py    which alerts fired, would they notify, were they useful (monthly; experiments/README.md)
reports/alerts/            its reports, one per cluster and month, and the human verdicts (labels.yaml)
```

## Changes and who makes them

| Change | How |
|---|---|
| New version in **dev** | Application CI after merging to main: commit `<app> dev: <image>=<tag>` (repo-scoped deploy key) |
| **prod** | Actions → `promote` (app, dev → prod) → PR → review & merge |
| Rollback | `git revert` the commit → Argo CD syncs the previous version |
| Configuration | PR to `base/` or an overlay (validated by CI) |

No secrets live here: manifests reference Secrets (`secretKeyRef`, `imagePullSecrets`) created by the platform.
