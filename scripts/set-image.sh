#!/usr/bin/env bash
# 应用的 CI 用: <app> 的 <env> 的镜像 tag 改成新的(只动 images 的 newTag)
#   usage: scripts/set-image.sh <app> <env> <image>=<tag> [...]
set -euo pipefail
cd "$(dirname "$0")/.."
app=$1; env=$2; shift 2
f=$app/overlays/$env/kustomization.yaml
for kv in "$@"; do
  img=${kv%%=*}; tag=${kv#*=}
  python3 - "$f" "$img" "$tag" <<'PY'
import re, sys
f, img, tag = sys.argv[1:]
s = open(f).read()
s, n = re.subn(rf"(- name: {re.escape(img)}\n\s+newTag: )\S+", rf"\g<1>{tag}", s)
if n != 1:
    sys.exit(f"{f}: image {img} not found")
open(f, "w").write(s)
PY
done
