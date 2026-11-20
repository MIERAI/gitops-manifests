#!/usr/bin/env bash
# After forking this repository: point it at your fork (Argo CD Applications, the ApplicationSet, the scripts), so the
# clusters you build from it sync YOUR copy. Run once, commit, push; then build the demo cluster with
# TF_VAR_manifests_repo=https://github.com/<you>/gitops-manifests.git (infrastructure-live tofu/demo).
#   usage: scripts/fork.sh <github owner>
set -euo pipefail
cd "$(dirname "$0")/.."
owner=${1:?usage: scripts/fork.sh <github owner>}
from=https://github.com/MIERAI/gitops-manifests.git
to=https://github.com/$owner/gitops-manifests.git
files=$(git grep -l -F "$from" || true)
[ -n "$files" ] || { echo "nothing to change (already forked?)"; exit 0; }
for f in $files; do
  python3 - "$f" "$from" "$to" <<'PY'
import sys
f, a, b = sys.argv[1:]
s = open(f).read()
open(f, "w").write(s.replace(a, b))
PY
  echo "• $f"
done
echo "now: git commit -am 'point at $to' && git push"
