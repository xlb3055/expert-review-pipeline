#!/bin/bash
# 专家考核产物自动审核流水线 — 入口脚本
# 在火山引擎持续交付流水线中执行

set -euo pipefail

RECORD_ID="${RECORD_ID:-}"
[ -z "$RECORD_ID" ] && { echo "错误: RECORD_ID 未设置" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

python3 -c "import requests" 2>/dev/null || pip install -q requests
python3 -c "import yaml" 2>/dev/null || pip install -q pyyaml
python3 -c "import daytona_sdk" 2>/dev/null || python3 -c "import daytona" 2>/dev/null || pip install -q daytona-sdk
mkdir -p /workspace

python3 -m core.pipeline_runner --project-dir "$SCRIPT_DIR" --record-id "$RECORD_ID"
