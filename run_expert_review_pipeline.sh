#!/bin/bash
# 专家考核产物自动审核流水线 — 入口脚本
# 在火山引擎持续交付流水线中执行
# 注意：敏感信息通过流水线「变量/密钥」配置，不要提交到 Git

set -euo pipefail

PIPELINE_START=$(date +%s)
echo "===== 专家考核评审流水线 ====="
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"

# ---------- 接收 record_id ----------
export RECORD_ID="${RECORD_ID:-}"
if [ -z "$RECORD_ID" ]; then
    echo "错误: RECORD_ID 未设置" >&2
    exit 1
fi
echo "Record ID: $RECORD_ID"

# ---------- 阶段 0: 检查环境 + 安装依赖 ----------
echo ""
STAGE_START=$(date +%s)
echo "===== 阶段0: 环境准备 ====="
python3 --version

echo "--- 检查依赖 ---"
python3 -c "import requests; print('requests OK')" 2>/dev/null || { echo "安装 requests..."; pip install -q requests; }
python3 -c "import daytona_sdk; print('daytona-sdk OK')" 2>/dev/null || python3 -c "import daytona; print('daytona OK')" 2>/dev/null || { echo "安装 daytona-sdk..."; pip install -q daytona-sdk; }

# 确定脚本目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "脚本目录: $SCRIPT_DIR"

# 确保工作目录存在
mkdir -p /workspace

echo "阶段0 耗时: $(($(date +%s) - STAGE_START))s"

# ---------- 阶段 1: 粗筛 ----------
echo ""
STAGE_START=$(date +%s)
echo "===== 阶段1: 脚本粗筛 ====="

set +e
python3 "$SCRIPT_DIR/pre_screen.py" --record-id "$RECORD_ID"
PRE_EXIT=$?
set -e

echo "粗筛退出码: $PRE_EXIT"

case $PRE_EXIT in
    0)
        echo "粗筛结果: 通过 → 继续 AI 评审"
        ;;
    1)
        echo "粗筛结果: 拒绝 → 流水线结束"
        echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
        exit 0
        ;;
    2)
        echo "粗筛结果: 待人工复核 → 继续 AI 评审"
        ;;
    *)
        echo "粗筛结果: 系统错误 (exit=$PRE_EXIT)" >&2
        exit 1
        ;;
esac

echo "阶段1 耗时: $(($(date +%s) - STAGE_START))s"

# ---------- 阶段 2: AI 评审 ----------
echo ""
STAGE_START=$(date +%s)
echo "===== 阶段2: AI 评审 ====="

set +e
python3 "$SCRIPT_DIR/ai_review.py" --record-id "$RECORD_ID"
AI_EXIT=$?
set -e

echo "AI 评审退出码: $AI_EXIT"

if [ $AI_EXIT -ne 0 ]; then
    echo "警告: AI 评审失败 (exit=$AI_EXIT)，继续回填已有结果" >&2
fi

echo "阶段2 耗时: $(($(date +%s) - STAGE_START))s"

# ---------- 阶段 3: 结果回填 ----------
echo ""
STAGE_START=$(date +%s)
echo "===== 阶段3: 结果回填 ====="

python3 "$SCRIPT_DIR/writeback.py" \
    --record-id "$RECORD_ID" \
    --pre-screen-result /workspace/pre_screen_result.json \
    --ai-review-result /workspace/ai_review_result.json

echo "阶段3 耗时: $(($(date +%s) - STAGE_START))s"

echo ""
echo "===== 流水线完成 ====="
echo "总耗时: $(($(date +%s) - PIPELINE_START))s"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
