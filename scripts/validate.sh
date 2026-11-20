#!/usr/bin/env bash
# Every application must render as Argo CD renders it (scripts/render.py: kustomize overlay or Helm chart + values) and
# pass kubeconform (CRDs such as Application / Rollout via the CRDs catalog).
# platform/ (upstream install manifests) is only built, not schema-checked.
# Admission policies (platform/policies): their own tests (good/bad fixtures), then every application whose
# namespace is enforced must pass them — the same check Kyverno does at admission, but on the pull request.
set -euo pipefail
cd "$(dirname "$0")/.."
KC=${KUBECONFORM:-kubeconform}
KY=${KYVERNO:-kyverno}
CRDS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'   # Argo CD / Rollouts
PINNED='platform/pyrra/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'   # from our pinned CRD (the catalog's is older)
mapfile -t APPS < <(python3 -I scripts/render.py list | awk '$2 !~ /^platform\// || $2 == "platform/slo" {print $1}')   # applications (and their SLOs), not the platform
for a in "${APPS[@]}"; do
  echo "• $a"
  python3 -I scripts/render.py render "$a" | $KC -strict -summary -schema-location default -schema-location "$PINNED" -schema-location "$CRDS" -
done
# Applications: apps/ (kind) and every cluster built on it (clusters/<name>)
for a in apps/ clusters/*/; do
  echo "• $a"
  kubectl kustomize "$a" | $KC -strict -summary -schema-location default -schema-location "$CRDS" -
done
for p in platform/*/; do
  [ -f "$p/Chart.yaml" ] && continue   # a chart (platform/slo): rendered above, with each application's values
  echo "• $p"; kubectl kustomize "$p" > /dev/null
done

echo "• experiments"   # parse, and the fields the runner needs (scripts/experiment.py)
python3 -I - <<'PY'
import glob, sys, yaml
for f in sorted(glob.glob("experiments/*.yaml")):
    x = yaml.safe_load(open(f))
    missing = [k for k in ("hypothesis", "namespace", "expect") if k not in x]
    if missing:
        sys.exit(f"{f}: missing {missing}")
    print(f"  {f}: ok")
PY

echo "• alert report"   # scripts/alert-report.py on synthetic fixtures (episodes, routing, classification)
python3 -I scripts/test_alert_report.py

echo "• admission policies"
$KY test platform/policies/tests
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# enforced namespaces: Applications that set the label (apps/*.yaml), and every app.yaml application
python3 -I - > "$tmp/values.yaml" <<'PY'
import sys
sys.path.insert(0, "scripts")
import render
print("apiVersion: cli.kyverno.io/v1alpha1\nkind: Values\nnamespaceSelector:")
for ns in sorted({a["namespace"] for a in render.apps() if a["enforced"]}):
    print(f"  - {{ name: {ns}, labels: {{ gitops-platform/policies: enforced }} }}")
PY
args=()
for a in "${APPS[@]}"; do python3 -I scripts/render.py render "$a" > "$tmp/$a.yaml"; args+=(--resource "$tmp/$a.yaml"); done
out=$($KY apply platform/policies/workload-baseline.yaml --exception platform/policies/exceptions.yaml \
        --values-file "$tmp/values.yaml" "${args[@]}" 2>&1) || true
echo "$out" | grep -vE '^\s*$'
echo "$out" | grep -qE 'fail: 0, warn: 0, error: 0' || { echo "✗ policy violations (see above)"; exit 1; }
