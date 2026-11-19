#!/usr/bin/env bash
# 环境之间的升级: 把 <app> 在 <from> 跑过的一个版本搬到 <to>。只改 kustomization.yaml 的 images(PR 由 workflow 开)
#   usage: scripts/promote.sh <app> [from=dev] [to=prod] [version=<from> 现在的版本] [soak 分钟=0] [image]
# 版本 = <from> 第一个镜像的 tag(给了 image: 那个镜像的 tag)。只接受在 <from> 跑过的版本(回滚也一样: 选一个旧版本);
# 它在 <from> 连续运行的时间(从到达到被替换,或到现在)要 ≥ soak。
# 搬哪些镜像: 默认全部一起,用 <from> 跑这个版本时的那一组 tag(如 api-factory 的 api + 迁移镜像);给了 image 只搬它
# (monorepo 里各自发布的服务,如 storefront 的 web / api)。
# 全部从 git 历史推算(需要完整历史: actions/checkout fetch-depth 0)。输出: 搬过去的 <镜像>:<tag> ...
set -euo pipefail
cd "$(dirname "$0")/.."
app=$1; from=${2:-dev}; to=${3:-prod}; version=${4:-}; soak=${5:-0}; image=${6:-}
src=$app/overlays/$from/kustomization.yaml; dst=$app/overlays/$to/kustomization.yaml
python3 - "$src" "$dst" "$version" "$soak" "$image" <<'PY'
import re, subprocess, sys, time
src, dst, version, soak, image = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4] or 0), sys.argv[5]
IMG = re.compile(r"- name: (\S+)\n\s+newTag: \"?([^\"\s]+)\"?")

def images(text):
    found = IMG.findall(text)  # [(image, tag)], in file order: the first one is the version
    if image:  # only that image (its tag is the version)
        found = [(n, t) for n, t in found if n == image]
    return found

# history of the source overlay, newest first: (commit time, images)
log = subprocess.run(["git", "log", "--format=%H %ct", "--", src], check=True, capture_output=True, text=True).stdout.split()
hist = []
for sha, ct in zip(log[::2], log[1::2]):
    text = subprocess.run(["git", "show", f"{sha}:{src}"], capture_output=True, text=True).stdout
    if images(text):
        hist.append((int(ct), images(text)))
if not hist:
    sys.exit(f"{src}: no history" + (f" of image {image}" if image else ""))
version = version or hist[0][1][0][1]
# the newest unbroken stretch in which the source ran the version
idx = [i for i, (_, imgs) in enumerate(hist) if imgs[0][1] == version]
if not idx:
    sys.exit(f"{version} never ran in {src}: only versions that ran there can be promoted")
start = idx[0]
while start + 1 < len(hist) and hist[start + 1][1][0][1] == version:
    start += 1
arrived = hist[start][0]
left = hist[idx[0] - 1][0] if idx[0] > 0 else time.time()
ran = (left - arrived) / 60
if ran < soak:
    sys.exit(f"{version} ran {ran:.0f} min in {src}, needs {soak}: wait (or pass a smaller soak)")
tags = dict(hist[start][1])  # the set of images the source ran with that version
s = open(dst).read()
for name, tag in tags.items():
    s, n = re.subn(rf"(- name: {re.escape(name)}\n\s+newTag: )\S+", rf'\g<1>"{tag}"', s)
    if n != 1:
        sys.exit(f"{dst}: image {name} not found")
open(dst, "w").write(s)
print(" ".join(f"{k}:{v}" for k, v in tags.items()))
print(f"ran {ran:.0f} min in the source", file=sys.stderr)
PY
