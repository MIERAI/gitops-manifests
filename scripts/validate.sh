#!/usr/bin/env bash
# 每个 overlay: kustomize 能构建 + kubeconform 的 schema 校验(Argo CD 的 Application 用 CRD 的 schema)
set -euo pipefail
cd "$(dirname "$0")/.."
KC=${KUBECONFORM:-kubeconform}
for o in */overlays/*/; do
  echo "• $o"
  kubectl kustomize "$o" | $KC -strict -summary -ignore-missing-schemas -
done
$KC -strict -summary \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  apps/
