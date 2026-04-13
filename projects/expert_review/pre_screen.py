#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第一层：7 项硬门槛初筛

直接在火山流水线中执行（不需要 Daytona），速度快（2-3 秒）。
执行 7 项硬性校验，结果回填飞书多维表格。

退出码:
  0 = 通过（继续 AI 评审）
  1 = 拒绝（流水线结束）
  2 = 待人工复核（继续 AI 评审）
  3 = 系统错误

用法:
  python3 pre_screen.py --record-id <record_id> --project-dir <dir>
"""

import argparse
import json
import os
import re
import sys

from core.config_loader import load_project_config, get_field_name
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_attachment_file_token,
    extract_link_url,
)
from core.trace_parser import TraceAnalysis, parse_trace_file


# ---------- 拒绝关键词（纯 demo / hello world / 测试类任务） ----------

_REJECT_PATTERNS = re.compile(
    r"(^(hello\s*world|测试|test|demo|示例|example|样例)\s*$)",
    re.IGNORECASE,
)

# ---------- 密钥泄露正则 ----------
# 只匹配硬编码的密钥值（sk-xxx, ghp_xxx 等），不匹配代码中的变量赋值

_SECRET_PATTERNS = re.compile(
    r"("
    r"sk-[a-zA-Z0-9]{20,}"           # OpenAI / Anthropic 密钥
    r"|ghp_[a-zA-Z0-9]{36,}"         # GitHub Personal Access Token
    r"|gho_[a-zA-Z0-9]{36,}"         # GitHub OAuth Token
    r"|xoxb-[a-zA-Z0-9\-]{20,}"      # Slack Bot Token
    r"|AKIA[0-9A-Z]{16}"             # AWS Access Key
    r"|-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"  # PEM 私钥
    r")",
    re.IGNORECASE,
)

# ---------- 验证类工具关键词 ----------

_VERIFICATION_KEYWORDS = re.compile(
    r"(bash|execute|test|run|preview|terminal|shell|npm\s+test|pytest|"
    r"jest|cargo\s+test|go\s+test|make\s+test|python.*-m\s+pytest|"
    r"node\s|python3?\s|java\s|javac|gcc|g\+\+|rustc|"
    r"curl|wget|open\s|xdg-open|start\s)",
    re.IGNORECASE,
)


# ---------- 7 项检查 ----------

def check_task_authenticity(fields: dict, desc_field: str, min_length: int) -> dict:
    """检查 1: 任务描述 >= min_length 字，且不含纯 demo/hello world/测试。"""
    desc = normalize_field_value(fields.get(desc_field, ""))
    length = len(desc)

    if length < min_length:
        return {
            "check": "task_authenticity",
            "passed": False,
            "detail": f"任务描述长度 {length} < {min_length}，请补充任务描述",
            "action": "reject",
        }

    desc_stripped = desc.strip()
    if _REJECT_PATTERNS.search(desc_stripped):
        return {
            "check": "task_authenticity",
            "passed": False,
            "detail": "任务描述为纯 demo/hello world/测试类内容，不满足真实任务要求",
            "action": "reject",
        }

    return {
        "check": "task_authenticity",
        "passed": True,
        "detail": f"任务描述长度 {length} >= {min_length}，内容合规",
    }


def check_trace_integrity(fields: dict, trace_field_name: str,
                           trace: TraceAnalysis, min_rounds: int) -> dict:
    """检查 2: Trace 存在 + 可解析 + 轮次 >= min_rounds。"""
    trace_field = fields.get(trace_field_name)
    file_token = extract_attachment_file_token(trace_field)

    if not file_token:
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


def check_final_product_exists(fields: dict, product_field: str,
                                attachment_field: str) -> dict:
    """检查 4: 最终产物链接或附件不为空。"""
    link = extract_link_url(fields.get(product_field, ""))
    attachment = extract_attachment_file_token(fields.get(attachment_field))

    if link or attachment:
        source = "链接" if link else "附件"
        return {
            "check": "final_product_exists",
            "passed": True,
            "detail": f"最终产物存在（{source}）",
        }
    return {
        "check": "final_product_exists",
        "passed": False,
        "detail": "最终产物链接和附件均为空，请提供其中一项",
        "action": "reject",
    }


def check_verification_exists(trace_content: str) -> dict:
    """检查 5: Trace 中有验证类工具调用（Bash/execute/terminal 等执行类工具）。

    只检查 tool_use 块中的工具名称，不搜索代码内容，避免误报。
    """
    verification_tools = {"bash", "execute", "terminal", "shell"}
    found = False

    for line in trace_content.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(entry, dict):
            continue

        # 检查 assistant 消息中 tool_use 块的工具名
        if entry.get("type") == "assistant":
            for content_source in (entry.get("content", []),
                                    entry.get("message", {}).get("content", []) if isinstance(entry.get("message"), dict) else []):
                if isinstance(content_source, list):
                    for block in content_source:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_name = (block.get("name") or "").lower()
                            if tool_name in verification_tools:
                                found = True
                                break
                            # 也检查 Bash 工具的输入中是否有测试/运行命令
                            if tool_name in ("bash",):
                                found = True
                                break
                if found:
                    break

        # 检查顶层 tool_use 类型
        if entry.get("type") == "tool_use":
            tool_name = (entry.get("name") or "").lower()
            if tool_name in verification_tools:
                found = True

        if found:
            break

    if found:
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


def check_compliance(trace_content: str) -> dict:
    """检查 7: Trace 中不含明显密钥模式。"""
    match = _SECRET_PATTERNS.search(trace_content)
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

def run_pre_screen(record_id: str, project_dir: str) -> int:
    """
    执行粗筛流程。

    返回退出码: 0=通过, 1=拒绝, 2=待复核, 3=系统错误
    """
    config = load_project_config(project_dir)
    client = FeishuClient.from_config(config)
    fm = config.get("field_mapping", {})
    pre_cfg = config.get("pre_screen", {})
    workspace = config.get("workspace", {})

    trace_output_path = os.environ.get(
        "TRACE_OUTPUT_PATH",
        workspace.get("trace_path", "/workspace/trace.jsonl"),
    )
    result_path = os.environ.get(
        "PRE_SCREEN_RESULT_PATH",
        workspace.get("pre_screen_result_path", "/workspace/pre_screen_result.json"),
    )
    min_rounds = pre_cfg.get("min_conversation_rounds", 3)
    min_desc_len = pre_cfg.get("min_task_description_length", 50)

    print("===== 硬门槛初筛开始 =====")
    print(f"Record ID: {record_id}")

    # 1. 获取飞书记录
    print("\n--- 获取飞书记录 ---")
    record = client.get_record(record_id)
    fields = record.get("fields", {})

    results = []

    # 检查 1: 任务真实性
    desc_field = get_field_name(config, "task_description")
    check1 = check_task_authenticity(fields, desc_field, min_desc_len)
    results.append(check1)
    print(f"[检查1] task_authenticity: {'通过' if check1['passed'] else '不通过'} — {check1['detail']}")

    # 检查前先判断 Trace 附件是否存在，如存在则下载解析
    trace_field_name = get_field_name(config, "trace_file")
    trace_field = fields.get(trace_field_name)
    file_token = extract_attachment_file_token(trace_field)
    trace = TraceAnalysis()  # 默认空
    trace_content = ""

    if file_token:
        print("\n--- 下载 Trace 附件 ---")
        output_dir = os.path.dirname(trace_output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        try:
            client.download_attachment(file_token, trace_output_path)
            trace = parse_trace_file(trace_output_path)
            print(f"Trace 解析结果: 轮次={trace.conversation_rounds}, 模型={trace.model_name}, "
                  f"工具调用={trace.tool_call_count}, 总行数={trace.total_lines}")
            # 读取原始内容用于合规检查和验证检查
            try:
                with open(trace_output_path, "r", encoding="utf-8") as f:
                    trace_content = f.read()
            except Exception:
                trace_content = ""
        except Exception as e:
            results.append({
                "check": "trace_download",
                "passed": False,
                "detail": f"Trace 下载失败: {e}",
                "action": "reject",
            })
            return _finalize(client, record_id, results, "拒绝", result_path, fm)

    # 检查 2: Trace 完整性
    check2 = check_trace_integrity(fields, trace_field_name, trace, min_rounds)
    results.append(check2)
    print(f"[检查2] trace_integrity: {'通过' if check2['passed'] else '不通过'} — {check2['detail']}")

    # 检查 3: 工具闭环
    check3 = check_tool_loop_exists(trace)
    results.append(check3)
    print(f"[检查3] tool_loop_exists: {'通过' if check3['passed'] else '不通过'} — {check3['detail']}")

    # 检查 4: 最终产物
    product_field = get_field_name(config, "final_product")
    attachment_field = get_field_name(config, "final_attachment")
    check4 = check_final_product_exists(fields, product_field, attachment_field)
    results.append(check4)
    print(f"[检查4] final_product_exists: {'通过' if check4['passed'] else '不通过'} — {check4['detail']}")

    # 检查 5: 验证类工具调用
    check5 = check_verification_exists(trace_content)
    results.append(check5)
    print(f"[检查5] verification_exists: {'通过' if check5['passed'] else '不通过'} — {check5['detail']}")

    # 检查 6: Trace 与产物一致性
    has_product = bool(
        extract_link_url(fields.get(product_field, ""))
        or extract_attachment_file_token(fields.get(attachment_field))
    )
    check6 = check_trace_product_consistent(trace, has_product)
    results.append(check6)
    print(f"[检查6] trace_product_consistent: {'通过' if check6['passed'] else '不通过'} — {check6['detail']}")

    # 检查 7: 合规性
    check7 = check_compliance(trace_content)
    results.append(check7)
    print(f"[检查7] compliance_check: {'通过' if check7['passed'] else '不通过'} — {check7['detail']}")

    # 汇总结果
    rejected = [r for r in results if not r["passed"] and r.get("action") == "reject"]
    manual_review = [r for r in results if not r["passed"] and r.get("action") == "manual_review"]

    if rejected:
        return _finalize(client, record_id, results, "拒绝", result_path, fm)
    elif manual_review:
        return _finalize(client, record_id, results, "待人工复核", result_path, fm)
    else:
        return _finalize(client, record_id, results, "通过", result_path, fm)


def _finalize(client: FeishuClient, record_id: str, results: list, status: str,
              result_path: str, field_mapping: dict) -> int:
    """汇总结果、回填飞书、保存本地 JSON、返回退出码。"""
    result_obj = {
        "粗筛状态": status,
        "checks": results,
        "passed_count": sum(1 for r in results if r["passed"]),
        "total_count": len(results),
    }

    # 保存本地结果
    result_dir = os.path.dirname(result_path)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=2)
    print(f"\n粗筛结果已保存: {result_path}")

    # 回填飞书
    status_field = field_mapping.get("pre_screen_status", "粗筛状态")
    detail_field = field_mapping.get("pre_screen_detail", "粗筛详情")

    print(f"\n--- 回填飞书 (粗筛状态={status}) ---")
    try:
        update_fields = {
            status_field: status,
            detail_field: json.dumps(result_obj, ensure_ascii=False, indent=2),
        }
        client.update_record(record_id, update_fields)
        print("飞书回填成功")
    except Exception as e:
        print(f"飞书回填失败: {e}", file=sys.stderr)

    print(f"\n===== 硬门槛初筛结束: {status} =====")

    if status == "拒绝":
        return 1
    elif status == "待人工复核":
        return 2
    else:
        return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物硬门槛初筛")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    args = parser.parse_args()

    try:
        exit_code = run_pre_screen(args.record_id, args.project_dir)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 3

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
