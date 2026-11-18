#!/usr/bin/env bash
# CI tools for scripts/validate.sh (Linux x86_64 runners), pinned versions.
set -euo pipefail
dest=${1:-/usr/local/bin}
curl -fsSL https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz | tar xz -C "$dest" kubeconform
curl -fsSL https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_linux_x86_64.tar.gz | tar xz -C "$dest" kyverno
