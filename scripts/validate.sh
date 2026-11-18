#!/usr/bin/env bash
# Every overlay must build with kustomize and pass kubeconform (CRDs such as Application / Rollout via the CRDs catalog).
# platform/ (upstream install manifests) is only built, not schema-checked.
set -euo pipefail
cd "$(dirname "$0")/.."
KC=${KUBECONFORM:-kubeconform}
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'   # Argo CD / Rollouts
for o in */overlays/*/; do
  echo "• $o"
  kubectl kustomize "$o" | $KC -strict -summary -schema-location default -schema-location "$CRDS" -
done
$KC -strict -summary \
  -schema-location default \
  -schema-location "$CRDS" \
  apps/
for p in platform/*/; do echo "• $p"; kubectl kustomize "$p" > /dev/null; done
