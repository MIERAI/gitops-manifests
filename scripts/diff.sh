#!/usr/bin/env bash
# 渲染后的清单的差分(PR 的说明 / 评论用): <base> 和工作区,每个 overlay 各自 kustomize build 后比较。输出 Markdown
#   usage: scripts/diff.sh <base-ref>
set -euo pipefail
cd "$(dirname "$0")/.."
base=$1
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
git worktree add -q "$tmp/base" "$base" 2>/dev/null || git worktree add -q --detach "$tmp/base" "$base"
trap 'git worktree remove --force "$tmp/base" >/dev/null 2>&1; rm -rf "$tmp"' EXIT
changed=0
echo "### Rendered diff (\`kustomize build\`, against \`$base\`)"
for o in */overlays/*/; do
  o=${o%/}
  new=$(kubectl kustomize "$o" 2>&1 || true)
  old=$( [ -d "$tmp/base/$o" ] && kubectl kustomize "$tmp/base/$o" 2>&1 || true)
  d=$(diff -u <(printf '%s\n' "$old") <(printf '%s\n' "$new") | tail -n +3 || true)
  [ -z "$d" ] && continue
  changed=1
  echo; echo "<details><summary><code>$o</code> (+$(grep -c '^+' <<<"$d") / -$(grep -c '^-' <<<"$d"))</summary>"; echo
  echo '```diff'; printf '%s\n' "$d" | head -300; echo '```'; echo "</details>"
done
[ $changed = 1 ] || echo; [ $changed = 1 ] || echo "No change in the rendered manifests."
