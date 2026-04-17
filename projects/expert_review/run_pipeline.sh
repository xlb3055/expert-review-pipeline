#!/bin/bash
# 专家考核产物自动审核流水线 — 入口脚本
# 在火山引擎持续交付流水线中执行
#
# RECORD_ID 来源优先级：
#   1. 环境变量 RECORD_ID（CP 变量管理 / 手动传入）
#   2. 第一个命令行参数（方便本地调试: ./run_pipeline.sh recXXX）
#   3. 环境变量 WEBHOOK_BODY / TRIGGER_PAYLOAD 中解析 JSON

set -euo pipefail

RECORD_ID="${RECORD_ID:-}"

# 兼容命令行参数传入
if [ -z "$RECORD_ID" ] && [ -n "${1:-}" ]; then
    RECORD_ID="$1"
fi

# 兼容火山引擎 CP webhook body 注入为环境变量的场景
if [ -z "$RECORD_ID" ]; then
    for _body_var in WEBHOOK_BODY TRIGGER_PAYLOAD CP_TRIGGER_BODY; do
        _body="${!_body_var:-}"
        if [ -n "$_body" ]; then
            _extracted=$(echo "$_body" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('RECORD_ID', d.get('record_id', '')))
except: pass
" 2>/dev/null)
            if [ -n "$_extracted" ]; then
                RECORD_ID="$_extracted"
                echo "从 $_body_var 解析到 RECORD_ID=$RECORD_ID"
                break
            fi
        fi
    done
fi

[ -z "$RECORD_ID" ] && { echo "错误: RECORD_ID 未设置。请通过以下方式之一传入:
  1. 环境变量: export RECORD_ID=recXXX
  2. 命令行参数: ./run_pipeline.sh recXXX
  3. CP 变量管理: 添加 RECORD_ID 变量并在 webhook 触发器中配置运行时变量" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

python3 -c "import requests" 2>/dev/null || pip install -q requests
python3 -c "import yaml" 2>/dev/null || pip install -q pyyaml
python3 -c "import daytona_sdk" 2>/dev/null || python3 -c "import daytona" 2>/dev/null || pip install -q daytona-sdk
mkdir -p /workspace

python3 -m core.pipeline_runner --project-dir "$SCRIPT_DIR" --record-id "$RECORD_ID"
