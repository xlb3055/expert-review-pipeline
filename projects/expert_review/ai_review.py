#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第二层：AI 评审

在 Daytona 沙箱中执行 Claude Code，对专家考核产物进行双模块评分：
- 专家能力分（0-10）
- Trace 资产分（0-12）

Daytona 沙箱生命周期委托给 core.daytona_runner。

用法:
  python3 ai_review.py --record-id <record_id> --project-dir <dir>
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core.config_loader import load_project_config, get_field_name
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_link_url,
)
from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox
from core.trace_parser import truncate_trace_content


def _build_input_text(fields: dict, trace_content: str, config: dict) -> str:
    """组装 AI 评审的输入文本。"""
    fm = config.get("field_mapping", {})

    task_desc = normalize_field_value(fields.get(fm.get("task_description", "任务描述"), ""))
    expert_name = normalize_field_value(fields.get(fm.get("expert_name", "专家姓名"), ""))
    expert_id = normalize_field_value(fields.get(fm.get("expert_id", "专家ID"), ""))
    position = normalize_field_value(fields.get(fm.get("position", "岗位方向"), ""))
    product_link = extract_link_url(fields.get(fm.get("final_product", "最终产物"), ""))

    parts = [
        "# 专家考核产物 — AI 评审输入",
        "",
        "## 专家信息",
        f"- 姓名: {expert_name}",
        f"- ID: {expert_id}",
        f"- 岗位方向: {position}",
        "",
        "## 任务描述（专家撰写的 Prompt）",
        task_desc,
        "",
    ]

    if product_link:
        parts.extend([
            "## 最终产物链接",
            product_link,
            "",
        ])

    parts.extend([
        "## Claude Code Trace 日志",
        "以下是专家与 Claude Code 交互的完整 trace 记录（JSONL 格式）：",
        "",
        trace_content,
    ])

    return "\n".join(parts)


def run_ai_review(record_id: str, project_dir: str) -> int:
    """
    执行 AI 评审流程。

    返回: 0=成功, 1=失败
    """
    config = load_project_config(project_dir)
    client = FeishuClient.from_config(config)
    ai_cfg = config.get("ai_review", {})
    workspace = config.get("workspace", {})
    fm = config.get("field_mapping", {})

    trace_input_path = os.environ.get(
        "TRACE_OUTPUT_PATH",
        workspace.get("trace_path", "/workspace/trace.jsonl"),
    )
    result_path = os.environ.get(
        "AI_REVIEW_RESULT_PATH",
        workspace.get("ai_review_result_path", "/workspace/ai_review_result.json"),
    )

    # 构建 Daytona 运行配置
    sandbox_res = ai_cfg.get("sandbox_resources", {})
    run_config = DaytonaRunConfig(
        api_key=os.environ.get("DAYTONA_API_KEY", ""),
        snapshot=os.environ.get("SNAPSHOT_NAME", ai_cfg.get("sandbox_snapshot", "daytona-medium")),
        cpu=sandbox_res.get("cpu", 2),
        memory=sandbox_res.get("memory", 4),
        disk=sandbox_res.get("disk", 5),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api"),
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        model=os.environ.get(
            "ANTHROPIC_MODEL",
            os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ai_cfg.get("model", "") or "anthropic/claude-sonnet-4-6"),
        ),
        timeout=int(os.environ.get("CLAUDE_TIMEOUT", str(ai_cfg.get("timeout", 600)))),
    )

    t0 = time.time()
    print("===== AI 评审开始 =====")
    print(f"Record ID: {record_id}")
    print(f"模型: {run_config.model}")

    # 1. 获取飞书记录
    print("\n--- 获取飞书记录 ---")
    record = client.get_record(record_id)
    fields = record.get("fields", {})

    # 2. 读取 trace 内容
    print("\n--- 读取 Trace 内容 ---")
    trace_content = truncate_trace_content(trace_input_path, max_rounds=20, max_bytes=100000)
    print(f"Trace 内容长度: {len(trace_content)} 字符")

    # 3. 组装输入文本
    input_text = _build_input_text(fields, trace_content, config)
    print(f"输入文本总长度: {len(input_text)} 字符")

    # 4. 读取 prompt 和 schema
    prompt_file = Path(project_dir) / ai_cfg.get("prompt_file", "prompt.md")
    schema_file = Path(project_dir) / ai_cfg.get("schema_file", "schema.json")

    if not prompt_file.is_file():
        print(f"错误: prompt 文件不存在: {prompt_file}", file=sys.stderr)
        return 1
    if not schema_file.is_file():
        print(f"错误: schema 文件不存在: {schema_file}", file=sys.stderr)
        return 1

    prompt_content = prompt_file.read_text(encoding="utf-8")
    schema_content = schema_file.read_text(encoding="utf-8")

    # 5. 回填进行中状态
    ai_status_field = fm.get("ai_review_status", "AI评审状态")
    try:
        client.update_record(record_id, {ai_status_field: "进行中"})
    except Exception as e:
        print(f"回填进行中状态失败（非致命）: {e}")

    # 6. 在 Daytona 沙箱中执行 Claude
    print(f"\n--- 调用 Daytona 沙箱 --- [{time.time()-t0:.1f}s]")
    result = run_claude_in_sandbox(run_config, prompt_content, schema_content, input_text)

    if not result.success:
        print(f"AI 评审失败: {result.error}", file=sys.stderr)
        _save_error_result(result.error, result_path)
        return 1

    result_obj = result.result_json

    # 7. 解包 schema 包装 — 适配新双模块结构
    # 新 schema 顶层是 expert_ability + trace_asset
    # 如果返回了旧格式 expert_review_result 包装，则解包
    if "expert_review_result" in result_obj and "expert_ability" not in result_obj:
        result_obj = result_obj["expert_review_result"]

    # 8. 保存结果
    result_dir = os.path.dirname(result_path)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=2)
    print(f"\nAI 评审结果已保存: {result_path}")
    print(f"结果内容:\n{json.dumps(result_obj, ensure_ascii=False, indent=2)[:1000]}")
    print(f"总耗时: {result.elapsed_seconds:.1f}s")

    return 0


def _save_error_result(error_msg: str, result_path: str):
    """保存错误结果到本地文件。"""
    result = {
        "error": error_msg,
        "expert_ability": {
            "task_complexity": {"score": 0, "evidence": "评审失败"},
            "iteration_quality": {"score": 0, "evidence": "评审失败"},
            "professional_judgment": {"score": 0, "evidence": "评审失败"},
            "total": 0,
        },
        "trace_asset": {
            "authenticity": {"score": 0, "evidence": "评审失败"},
            "info_density": {"score": 0, "evidence": "评审失败"},
            "tool_loop": {"score": 0, "evidence": "评审失败"},
            "correction_value": {"score": 0, "evidence": "评审失败"},
            "verification_loop": {"score": 0, "evidence": "评审失败"},
            "compliance": {"score": 0, "evidence": "评审失败"},
            "total": 0,
        },
        "overall_assessment": f"AI 评审失败: {error_msg}",
        "trace_highlights": [],
    }
    result_dir = os.path.dirname(result_path)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="专家考核产物 AI 评审")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    args = parser.parse_args()

    try:
        exit_code = run_ai_review(args.record_id, args.project_dir)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        result_path = "/workspace/ai_review_result.json"
        _save_error_result(str(e), result_path)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
