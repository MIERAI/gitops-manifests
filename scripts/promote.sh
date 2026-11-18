#!/usr/bin/env bash
# 环境之间的升级: <app> 的 <from> 的镜像 tag → <to>。只改 kustomization.yaml 的 images(PR 由 workflow 开)
#   usage: scripts/promote.sh <app> [from=dev] [to=prod]
set -euo pipefail
cd "$(dirname "$0")/.."
app=$1; from=${2:-dev}; to=${3:-prod}
src=$app/overlays/$from/kustomization.yaml; dst=$app/overlays/$to/kustomization.yaml
python3 - "$src" "$dst" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
tags = dict(re.findall(r"- name: (\S+)\n\s+newTag: \"?([^\"\s]+)\"?", open(src).read()))
s = open(dst).read()
for name, tag in tags.items():
    s, n = re.subn(rf"(- name: {re.escape(name)}\n\s+newTag: )\S+", rf'\g<1>"{tag}"', s)
    if n != 1:
        sys.exit(f"{dst}: image {name} not found")
open(dst, "w").write(s)
print(" ".join(f"{k}:{v}" for k, v in tags.items()))
PY
