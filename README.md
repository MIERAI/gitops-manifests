# gitops-manifests

> Part of the [GitOps Platform Blueprint](https://github.com/MIERAI/gitops-platform-blueprint)

The desired state of every workload, per environment. Argo CD (installed by
[infrastructure-live](https://github.com/MIERAI/infrastructure-live)) watches this repository and keeps the cluster in sync.
**Changing the cluster = committing here.**

## Layout

```
apps/                      Argo CD Applications (app of apps): <app>-<env>, automated sync + prune + self-heal
api-factory/base/          Postgres (local only) → API (init container: goose migrations) → smoke Job (PostSync hook), ordered by sync waves
api-factory/overlays/      dev / prod: namespace, replicas, NodePort, image tags
sample-app/base|overlays/  the public reference app
scripts/set-image.sh       used by application CI (via the shared promote-dev workflow) to update a tag in dev
scripts/promote.sh         copies the tags of one environment to another (used by the promote workflow)
scripts/validate.sh        kustomize build + kubeconform for every overlay and Application (CI: validate)
```

## Changes and who makes them

| Change | How |
|---|---|
| New version in **dev** | Application CI after merging to main: commit `<app> dev: <image>=<tag>` (repo-scoped deploy key) |
| **prod** | Actions → `promote` (app, dev → prod) → PR → review & merge |
| Rollback | `git revert` the commit → Argo CD syncs the previous version |
| Configuration | PR to `base/` or an overlay (validated by CI) |

No secrets live here: manifests reference Secrets (`secretKeyRef`, `imagePullSecrets`) created by the platform.
