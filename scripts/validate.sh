#!/usr/bin/env bash
# Every overlay must build with kustomize and pass kubeconform (CRDs such as Application / Rollout via the CRDs catalog).
# platform/ (upstream install manifests) is only built, not schema-checked.
# Admission policies (platform/policies): their own tests (good/bad fixtures), then every overlay of an application whose
# namespace is enforced must pass them — the same check Kyverno does at admission, but on the pull request.
set -euo pipefail
cd "$(dirname "$0")/.."
KC=${KUBECONFORM:-kubeconform}
KY=${KYVERNO:-kyverno}
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'   # Argo CD / Rollouts
for o in */overlays/*/; do
  echo "• $o"
  kubectl kustomize "$o" | $KC -strict -summary -schema-location default -schema-location "$CRDS" -
done
# Applications: apps/ (kind) and every cluster built on it (clusters/<name>)
for a in apps/ clusters/*/; do
  echo "• $a"
  kubectl kustomize "$a" | $KC -strict -summary -schema-location default -schema-location "$CRDS" -
done
for p in platform/*/; do echo "• $p"; kubectl kustomize "$p" > /dev/null; done

echo "• admission policies"
$KY test platform/policies/tests
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# enforced namespaces = Applications that set the label through managedNamespaceMetadata
python3 - apps/*.yaml > "$tmp/values.yaml" <<'PY'
import re, sys
print("apiVersion: cli.kyverno.io/v1alpha1\nkind: Values\nnamespaceSelector:")
for f in sys.argv[1:]:
    s = open(f).read()
    if "gitops-platform/policies: enforced" in s:
        ns = re.search(r"destination:\s*\n(?:\s+\w+:.*\n)*?\s+namespace:\s*(\S+)", s).group(1)
        print(f"  - {{ name: {ns}, labels: {{ gitops-platform/policies: enforced }} }}")
PY
args=()
for o in */overlays/*/; do kubectl kustomize "$o" > "$tmp/$(echo "$o" | tr / _).yaml"; args+=(--resource "$tmp/$(echo "$o" | tr / _).yaml"); done
out=$($KY apply platform/policies/workload-baseline.yaml --exception platform/policies/exceptions.yaml \
        --values-file "$tmp/values.yaml" "${args[@]}" 2>&1) || true
echo "$out" | grep -vE '^\s*$'
echo "$out" | grep -qE 'fail: 0, warn: 0, error: 0' || { echo "✗ policy violations (see above)"; exit 1; }
