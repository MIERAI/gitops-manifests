#!/usr/bin/env bash
# 应用的 CI 用: <app> 的 <env> 的镜像 tag 改成新的(只动 tag 那一行)。哪个文件: <app>/app.yaml 或约定(scripts/images.py)
#   usage: scripts/set-image.sh <app> <env> <image>=<tag> [...]
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -P - "$@" <<'PY'
import sys
sys.path.insert(0, "scripts")
import images
app, env, pairs = sys.argv[1], sys.argv[2], sys.argv[3:]
for kv in pairs:
    img, tag = kv.split("=", 1)
    images.write(images.env_file(app, env, img), img, tag)
PY
