#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第一层：7 项硬门槛初筛

从 ctx_data.json 读取 feishu_fetch 拉到的字段，执行 7 项检查。
Trace 下载逻辑保留在此脚本内（属于业务逻辑）。
结果写回 ctx_data.json，由 feishu_writeback 统一回填。

退出码:
  0 = 通过（继续 AI 评审）
  1 = 拒绝（流水线结束）
  2 = 待人工复核（继续 AI 评审）
  3 = 系统错误

用法:
  python3 pre_screen.py --record-id <id> --project-dir <dir> --ctx-data-file <path>
"""

import argparse
import json
import os
import re
import sys

from core.ctx_utils import load_ctx_data, save_ctx_data
from core.config_loader import load_project_config
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_attachment_file_token,
    extract_attachment_url,
    extract_link_url,
)
from core.trace_parser import TraceAnalysis, parse_trace_file
from core.trace_extractor import extract_user_focused_content


# ---------- 拒绝关键词（纯 demo / hello world / 测试类任务） ----------

_REJECT_PATTERNS = re.compile(
    r"(^(hello\s*world|测试|test|demo|示例|example|样例|练习|exercise)\s*$)",
    re.IGNORECASE,
)

# ---------- 密钥泄露正则 ----------

_SECRET_PATTERNS = re.compile(
    r"("
    r"sk-[a-zA-Z0-9]{20,}"
    r"|ghp_[a-zA-Z0-9]{36,}"
    r"|gho_[a-zA-Z0-9]{36,}"
    r"|xoxb-[a-zA-Z0-9\-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"
    r")",
    re.IGNORECASE,
)


# ---------- 7 项检查 ----------

def check_task_authenticity(task_description: str) -> dict:
    """检查 1: 任务真实性 — 不是 demo/练习/拼凑题。"""
    if not task_description.strip():
        return {
            "check": "task_authenticity",
            "passed": False,
            "detail": "任务说明为空",
            "action": "reject",
        }

    if _REJECT_PATTERNS.search(task_description.strip()):
        return {
            "check": "task_authenticity",
            "passed": False,
            "detail": "任务描述为纯 demo/hello world/测试/练习类内容，不满足真实任务要求",
            "action": "reject",
        }

    return {
        "check": "task_authenticity",
        "passed": True,
        "detail": f"任务描述内容合规（{len(task_description)}字）",
    }


def check_trace_integrity(has_trace_file: bool,
                           trace: TraceAnalysis, min_rounds: int) -> dict:
    """检查 2: Trace 存在 + 可解析 + 轮次 >= min_rounds。"""
    if not has_trace_file:
        return {
            "check": "trace_integrity",
            "passed": False,
            "detail": "Trace 附件字段为空，请上传 .jsonl trace 文件",
            "action": "reject",
        }

    if not trace.is_valid:
        return {
            "check": "trace_integrity",
            "passed": False,
            "detail": f"Trace 解析失败: {'; '.join(trace.errors)}",
            "action": "reject",
        }

    if trace.conversation_rounds < min_rounds:
        return {
            "check": "trace_integrity",
            "passed": False,
            "detail": f"对话轮次 {trace.conversation_rounds} < {min_rounds}，不满足最低要求",
            "action": "reject",
        }

    return {
        "check": "trace_integrity",
        "passed": True,
        "detail": f"Trace 有效，对话轮次 {trace.conversation_rounds} >= {min_rounds}",
    }


def check_tool_loop_exists(trace: TraceAnalysis) -> dict:
    """检查 3: Trace 中至少 1 组 tool_use + tool_result 配对。"""
    if trace.has_tool_calls and trace.tool_call_count >= 1:
        return {
            "check": "tool_loop_exists",
            "passed": True,
            "detail": f"Trace 包含 {trace.tool_call_count} 次工具调用",
        }
    return {
        "check": "tool_loop_exists",
        "passed": False,
        "detail": "Trace 中未发现 tool_use + tool_result 配对，缺少工具调用记录",
        "action": "reject",
    }


def check_final_product_exists(raw_final_product) -> dict:
    """检查 4: 最终产物不为空。"""
    attachment = extract_attachment_file_token(raw_final_product)
    if attachment:
        return {
            "check": "final_product_exists",
            "passed": True,
            "detail": "最终产物存在（附件）",
        }

    link = extract_link_url(raw_final_product)
    if link:
        return {
            "check": "final_product_exists",
            "passed": True,
            "detail": "最终产物存在（链接）",
        }

    text = normalize_field_value(raw_final_product)
    if text.strip():
        return {
            "check": "final_product_exists",
            "passed": True,
            "detail": "最终产物存在（文本）",
        }

    return {
        "check": "final_product_exists",
        "passed": False,
        "detail": "最终产物字段为空，请提供最终产物",
        "action": "reject",
    }


def check_verification_exists(clean_trace: str) -> dict:
    """检查 5: 精简 trace 中有验证类工具调用（Bash/execute 等）。"""
    verification_keywords = re.compile(
        r"\[工具调用\]\s*(bash|execute|terminal|shell)",
        re.IGNORECASE,
    )

    if verification_keywords.search(clean_trace):
        return {
            "check": "verification_exists",
            "passed": True,
            "detail": "Trace 中包含验证类工具调用（Bash/execute 等）",
        }
    return {
        "check": "verification_exists",
        "passed": False,
        "detail": "Trace 中未发现 Bash/execute 等执行类工具调用，建议人工复核",
        "action": "manual_review",
    }


def check_trace_product_consistent(trace: TraceAnalysis, has_product: bool) -> dict:
    """检查 6: Trace 和最终产物都存在（深度一致性留给 AI）。"""
    if trace.is_valid and has_product:
        return {
            "check": "trace_product_consistent",
            "passed": True,
            "detail": "Trace 和最终产物均存在，深度一致性将由 AI 评审判断",
        }
    missing = []
    if not trace.is_valid:
        missing.append("Trace")
    if not has_product:
        missing.append("最终产物")
    return {
        "check": "trace_product_consistent",
        "passed": False,
        "detail": f"缺少 {' 和 '.join(missing)}，无法验证 Trace 与产物一致性",
        "action": "manual_review",
    }


def check_compliance(clean_trace: str) -> dict:
    """检查 7: 精简 trace 中不含明显密钥模式。"""
    match = _SECRET_PATTERNS.search(clean_trace)
    if match:
        snippet = match.group()[:30] + "..."
        return {
            "check": "compliance_check",
            "passed": False,
            "detail": f"Trace 中发现疑似密钥/凭据模式: {snippet}，建议人工复核",
            "action": "manual_review",
        }
    return {
        "check": "compliance_check",
        "passed": True,
        "detail": "未发现明显密钥泄露模式",
    }


# ---------- 主流程 ----------

def run_pre_screen(record_id: str, project_dir: str, ctx_data_file: str) -> int:
    """
    执行粗筛流程。

    从 ctx_data.json 读取数据，执行 7 项检查，结果写回 ctx_data.json。
    Trace 下载需要飞书 API（原始附件信息在 ctx_data 的 _raw_trace_file 中）。

    返回退出码: 0=通过, 1=拒绝, 2=待复核, 3=系统错误
    """
    config = load_project_config(project_dir)
    pre_cfg = config.get("pre_screen", {})
    workspace = config.get("workspace", {})
    min_rounds = pre_cfg.get("min_conversation_rounds", 3)

    # 从 ctx_data.json 读取数据
    ctx_data = load_ctx_data(ctx_data_file)

    trace_output_path = os.environ.get(
        "TRACE_OUTPUT_PATH",
        workspace.get("trace_path", os.path.join(os.path.dirname(ctx_data_file), "trace.jsonl")),
    )

    print("===== 硬门槛初筛开始 =====")
    print(f"Record ID: {record_id}")

    results = []

    # 检查 1: 任务真实性
    task_desc = ctx_data.get("task_description", "")
    check1 = check_task_authenticity(task_desc)
    results.append(check1)
    print(f"[检查1] task_authenticity: {'通过' if check1['passed'] else '不通过'} — {check1['detail']}")

    # 下载 Trace 附件（业务逻辑，需要飞书 API）
    raw_trace_file = ctx_data.get("_raw_trace_file")
    file_token = extract_attachment_file_token(raw_trace_file)
    trace = TraceAnalysis()
    clean_trace = ""

    if file_token:
        print("\n--- 下载 Trace 附件 ---")
        output_dir = os.path.dirname(trace_output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        try:
            client = FeishuClient.from_config(config)
            download_url = extract_attachment_url(raw_trace_file)
            client.download_attachment(file_token, trace_output_path,
                                       download_url=download_url or None)
            trace = parse_trace_file(trace_output_path)
            print(f"Trace 解析结果: 轮次={trace.conversation_rounds}, 模型={trace.model_name}, "
                  f"工具调用={trace.tool_call_count}, 总行数={trace.total_lines}")
            clean_trace = extract_user_focused_content(trace_output_path, max_bytes=500000)
            print(f"精简 Trace 内容: {len(clean_trace)} 字符")
        except Exception as e:
            results.append({
                "check": "trace_download",
                "passed": False,
                "detail": f"Trace 下载失败: {e}",
                "action": "reject",
            })
            return _finalize(ctx_data, ctx_data_file, results, "拒绝", clean_trace, trace_output_path)

    # 检查 2: Trace 完整性
    check2 = check_trace_integrity(bool(file_token), trace, min_rounds)
    results.append(check2)
    print(f"[检查2] trace_integrity: {'通过' if check2['passed'] else '不通过'} — {check2['detail']}")

    # 检查 3: 工具闭环
    check3 = check_tool_loop_exists(trace)
    results.append(check3)
    print(f"[检查3] tool_loop_exists: {'通过' if check3['passed'] else '不通过'} — {check3['detail']}")

    # 检查 4: 最终产物
    raw_final_product = ctx_data.get("_raw_final_product")
    if raw_final_product is None:
        raw_final_product = ctx_data.get("_raw_fields", {}).get("最终产物")
    check4 = check_final_product_exists(raw_final_product)
    results.append(check4)
    print(f"[检查4] final_product_exists: {'通过' if check4['passed'] else '不通过'} — {check4['detail']}")

    # 检查 5: 验证动作
    check5 = check_verification_exists(clean_trace)
    results.append(check5)
    print(f"[检查5] verification_exists: {'通过' if check5['passed'] else '不通过'} — {check5['detail']}")

    # 检查 6: Trace-产物一致
    has_product = bool(
        extract_attachment_file_token(raw_final_product)
        or extract_link_url(raw_final_product)
        or normalize_field_value(raw_final_product).strip()
    ) if raw_final_product else False
    check6 = check_trace_product_consistent(trace, has_product)
    results.append(check6)
    print(f"[检查6] trace_product_consistent: {'通过' if check6['passed'] else '不通过'} — {check6['detail']}")

    # 检查 7: 合规可用
    check7 = check_compliance(clean_trace)
    results.append(check7)
    print(f"[检查7] compliance_check: {'通过' if check7['passed'] else '不通过'} — {check7['detail']}")

    # 汇总结果
    rejected = [r for r in results if not r["passed"] and r.get("action") == "reject"]
    manual_review = [r for r in results if not r["passed"] and r.get("action") == "manual_review"]

    if rejected:
        return _finalize(ctx_data, ctx_data_file, results, "拒绝", clean_trace, trace_output_path)
    elif manual_review:
        return _finalize(ctx_data, ctx_data_file, results, "待人工复核", clean_trace, trace_output_path)
    else:
        return _finalize(ctx_data, ctx_data_file, results, "通过", clean_trace, trace_output_path)


def _finalize(ctx_data: dict, ctx_data_file: str, results: list,
              status: str, clean_trace: str, trace_output_path: str) -> int:
    """汇总结果写回 ctx_data.json，返回退出码。"""
    # 粗筛结果写入 ctx_data
    ctx_data["pre_screen_result"] = {
        "粗筛状态": status,
        "checks": results,
        "passed_count": sum(1 for r in results if r["passed"]),
        "total_count": len(results),
    }

    # 保存 trace 相关信息供后续阶段使用
    if clean_trace:
        ctx_data["trace_content"] = clean_trace
    if trace_output_path and os.path.isfile(trace_output_path):
        ctx_data["trace_output_path"] = trace_output_path

    # 如果粗筛拒绝，写入拒绝原因到 ctx_data 供 writeback 使用
    if status == "拒绝":
        reject_reasons = [r["detail"] for r in results if not r["passed"] and r.get("action") == "reject"]
        ctx_data["machine_review_note"] = "【粗筛拒绝】\n" + "\n".join(f"- {r}" for r in reject_reasons)
        ctx_data["review_status"] = "reject"

    save_ctx_data(ctx_data_file, ctx_data)
    print(f"\n粗筛结果已写回 ctx_data.json")
    print(f"===== 硬门槛初筛结束: {status} =====")

    if status == "拒绝":
        return 1
    elif status == "待人工复核":
        return 2
    else:
        return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物硬门槛初筛")
    parser.add_argument("--record-id", required=True, help="主表 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    parser.add_argument("--ctx-data-file", required=True, help="ctx_data.json 路径")
    args = parser.parse_args()

    try:
        exit_code = run_pre_screen(args.record_id, args.project_dir, args.ctx_data_file)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
