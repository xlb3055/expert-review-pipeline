#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第一层：脚本粗筛

直接在火山流水线中执行（不需要 Daytona），速度快（2-3 秒）。
执行 6 项硬性校验，结果回填飞书多维表格。

退出码:
  0 = 通过（继续 AI 评审）
  1 = 拒绝（流水线结束）
  2 = 待人工复核（继续 AI 评审）
  3 = 系统错误

用法:
  python3 pre_screen.py --record-id <record_id>
"""

import argparse
import json
import os
import sys
import tempfile

from feishu_utils import (
    check_required_env,
    download_attachment,
    extract_attachment_file_token,
    extract_link_url,
    get_feishu_token,
    get_record,
    normalize_field_value,
    update_record,
)
from trace_parser import TraceAnalysis, parse_trace_file

# ---------- 常量 ----------

TRACE_OUTPUT_PATH = os.environ.get("TRACE_OUTPUT_PATH", "/workspace/trace.jsonl")
PRE_SCREEN_RESULT_PATH = os.environ.get("PRE_SCREEN_RESULT_PATH", "/workspace/pre_screen_result.json")
MIN_CONVERSATION_ROUNDS = 5
MIN_TASK_DESCRIPTION_LENGTH = 100


# ---------- 6 项检查 ----------

def check_trace_exists(fields: dict) -> dict:
    """检查 1: Trace 附件字段不为空。"""
    trace_field = fields.get("Trace文件")
    file_token = extract_attachment_file_token(trace_field)
    if file_token:
        return {"check": "trace_exists", "passed": True, "detail": "Trace 附件存在"}
    return {"check": "trace_exists", "passed": False, "detail": "Trace 附件字段为空，请上传 .jsonl trace 文件", "action": "reject"}


def check_conversation_rounds(trace: TraceAnalysis) -> dict:
    """检查 2: 对话轮次 >= 5。"""
    if trace.conversation_rounds >= MIN_CONVERSATION_ROUNDS:
        return {
            "check": "conversation_rounds",
            "passed": True,
            "detail": f"对话轮次 {trace.conversation_rounds} >= {MIN_CONVERSATION_ROUNDS}",
        }
    return {
        "check": "conversation_rounds",
        "passed": False,
        "detail": f"对话轮次 {trace.conversation_rounds} < {MIN_CONVERSATION_ROUNDS}，不满足最低要求",
        "action": "reject",
    }


def check_model_sota(trace: TraceAnalysis) -> dict:
    """检查 3: 模型为 claude-opus 系列。"""
    if trace.is_sota_model:
        return {
            "check": "model_sota",
            "passed": True,
            "detail": f"模型 {trace.model_name} 为 opus 系列",
        }
    if not trace.model_name:
        # trace 中未记录模型信息（Claude Code transcript 的常见情况），标记待复核
        return {
            "check": "model_sota",
            "passed": False,
            "detail": "Trace 中未检测到模型信息，无法自动判定是否使用 opus，需人工确认",
            "action": "manual_review",
        }
    return {
        "check": "model_sota",
        "passed": False,
        "detail": f"模型 {trace.model_name} 不是 claude-opus 系列，请使用 claude-opus 模型",
        "action": "reject",
    }


def check_final_product_exists(fields: dict) -> dict:
    """检查 4: 最终产物链接或附件不为空。"""
    link = extract_link_url(fields.get("最终产物", ""))
    attachment = extract_attachment_file_token(fields.get("最终附件"))

    if link or attachment:
        source = "链接" if link else "附件"
        return {"check": "final_product_exists", "passed": True, "detail": f"最终产物存在（{source}）"}
    return {
        "check": "final_product_exists",
        "passed": False,
        "detail": "最终产物链接和附件均为空，请提供其中一项",
        "action": "reject",
    }


def check_task_description_length(fields: dict) -> dict:
    """检查 5: 任务描述 >= 100 字。"""
    desc = normalize_field_value(fields.get("任务描述", ""))
    length = len(desc)
    if length >= MIN_TASK_DESCRIPTION_LENGTH:
        return {
            "check": "task_description_length",
            "passed": True,
            "detail": f"任务描述长度 {length} >= {MIN_TASK_DESCRIPTION_LENGTH}",
        }
    return {
        "check": "task_description_length",
        "passed": False,
        "detail": f"任务描述长度 {length} < {MIN_TASK_DESCRIPTION_LENGTH}，请补充任务描述",
        "action": "reject",
    }


def check_trace_authenticity(trace: TraceAnalysis) -> dict:
    """检查 6: 包含工具调用记录（真实性校验）。"""
    if trace.has_tool_calls:
        return {
            "check": "trace_authenticity",
            "passed": True,
            "detail": f"Trace 包含 {trace.tool_call_count} 次工具调用",
        }
    return {
        "check": "trace_authenticity",
        "passed": False,
        "detail": "Trace 中未发现工具调用记录，可能不是真实的 Claude Code 操作日志",
        "action": "manual_review",
    }


# ---------- 主流程 ----------

def run_pre_screen(record_id: str) -> int:
    """
    执行粗筛流程。

    返回退出码: 0=通过, 1=拒绝, 2=待复核, 3=系统错误
    """
    check_required_env()

    print("===== 粗筛开始 =====")
    print(f"Record ID: {record_id}")

    # 1. 获取飞书 token 和记录
    print("\n--- 获取飞书记录 ---")
    token = get_feishu_token()
    record = get_record(token, record_id)
    fields = record.get("fields", {})

    # 2. 检查 1: Trace 附件存在性
    results = []
    check1 = check_trace_exists(fields)
    results.append(check1)
    print(f"[检查1] trace_exists: {'通过' if check1['passed'] else '不通过'} — {check1['detail']}")

    if not check1["passed"]:
        return _finalize(token, record_id, results, "拒绝")

    # 3. 下载 Trace 附件
    print("\n--- 下载 Trace 附件 ---")
    trace_field = fields.get("Trace文件")
    file_token = extract_attachment_file_token(trace_field)

    # 确保输出目录存在
    output_dir = os.path.dirname(TRACE_OUTPUT_PATH)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    try:
        download_attachment(token, file_token, TRACE_OUTPUT_PATH)
    except Exception as e:
        results.append({"check": "trace_download", "passed": False, "detail": f"Trace 下载失败: {e}", "action": "reject"})
        return _finalize(token, record_id, results, "拒绝")

    # 4. 解析 Trace
    print("\n--- 解析 Trace ---")
    trace = parse_trace_file(TRACE_OUTPUT_PATH)
    if not trace.is_valid:
        results.append({
            "check": "trace_parse",
            "passed": False,
            "detail": f"Trace 解析失败: {'; '.join(trace.errors)}",
            "action": "reject",
        })
        return _finalize(token, record_id, results, "拒绝")

    print(f"Trace 解析结果: 轮次={trace.conversation_rounds}, 模型={trace.model_name}, "
          f"工具调用={trace.tool_call_count}, 总行数={trace.total_lines}")

    # 5. 执行检查 2-6
    check2 = check_conversation_rounds(trace)
    results.append(check2)
    print(f"[检查2] conversation_rounds: {'通过' if check2['passed'] else '不通过'} — {check2['detail']}")

    check3 = check_model_sota(trace)
    results.append(check3)
    print(f"[检查3] model_sota: {'通过' if check3['passed'] else '不通过'} — {check3['detail']}")

    check4 = check_final_product_exists(fields)
    results.append(check4)
    print(f"[检查4] final_product_exists: {'通过' if check4['passed'] else '不通过'} — {check4['detail']}")

    check5 = check_task_description_length(fields)
    results.append(check5)
    print(f"[检查5] task_description_length: {'通过' if check5['passed'] else '不通过'} — {check5['detail']}")

    check6 = check_trace_authenticity(trace)
    results.append(check6)
    print(f"[检查6] trace_authenticity: {'通过' if check6['passed'] else '不通过'} — {check6['detail']}")

    # 6. 汇总结果
    rejected = [r for r in results if not r["passed"] and r.get("action") == "reject"]
    manual_review = [r for r in results if not r["passed"] and r.get("action") == "manual_review"]

    if rejected:
        return _finalize(token, record_id, results, "拒绝")
    elif manual_review:
        return _finalize(token, record_id, results, "待人工复核")
    else:
        return _finalize(token, record_id, results, "通过")


def _finalize(token: str, record_id: str, results: list, status: str) -> int:
    """汇总结果、回填飞书、保存本地 JSON、返回退出码。"""
    result_obj = {
        "粗筛状态": status,
        "checks": results,
        "passed_count": sum(1 for r in results if r["passed"]),
        "total_count": len(results),
    }

    # 保存本地结果
    result_dir = os.path.dirname(PRE_SCREEN_RESULT_PATH)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(PRE_SCREEN_RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=2)
    print(f"\n粗筛结果已保存: {PRE_SCREEN_RESULT_PATH}")

    # 回填飞书
    print(f"\n--- 回填飞书 (粗筛状态={status}) ---")
    try:
        update_fields = {
            "粗筛状态": status,
            "粗筛详情": json.dumps(result_obj, ensure_ascii=False, indent=2),
        }
        update_record(token, record_id, update_fields)
        print("飞书回填成功")
    except Exception as e:
        print(f"飞书回填失败: {e}", file=sys.stderr)

    print(f"\n===== 粗筛结束: {status} =====")

    if status == "拒绝":
        return 1
    elif status == "待人工复核":
        return 2
    else:
        return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物粗筛")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    args = parser.parse_args()

    try:
        exit_code = run_pre_screen(args.record_id)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
