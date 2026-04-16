#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第二层：AI 评审

本文件只保留 expert_review 项目的业务包装逻辑：
1. 从飞书主表读取记录
2. 下载 / 提取 Trace
3. 组装 trace 评审输入文本
4. 调用通用 AI 评审节点执行
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.config_loader import get_field_name, load_project_config
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_link_url,
    extract_attachment_file_tokens,
)
from core.trace_bundle import download_and_merge_trace_attachments
from core.generic_ai_review import DEFAULT_MODEL, GenericAIReviewRequest, run_generic_ai_review
from core.trace_extractor import extract_user_focused_content
from projects.expert_review.result_utils import normalize_ai_result


DEFAULT_ERROR_PATH = "/workspace/ai_review_error.json"


def _build_input_text(fields: dict, trace_content: str, config: dict) -> str:
    """组装 AI 评审输入文本。"""
    mfm = config.get("field_mapping", {})

    task_desc = normalize_field_value(fields.get(mfm.get("task_description", "任务说明"), ""))
    expert_name = normalize_field_value(fields.get(mfm.get("expert_name", "提交人"), ""))
    expert_id = normalize_field_value(fields.get(mfm.get("expert_id", "talent_id"), ""))
    position = normalize_field_value(fields.get(mfm.get("position", "岗位方向"), ""))

    product_field = mfm.get("final_product", "最终产物")
    product_value = fields.get(product_field, "")
    product_link = extract_link_url(product_value)
    if not product_link:
        product_link = normalize_field_value(product_value)

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
            "## 最终产物",
            product_link,
            "",
        ])

    parts.extend([
        "## Claude Code Trace 日志",
        "以下是专家与 Claude Code 交互的精简 trace 记录（用户消息 + 工具调用摘要）：",
        "",
        trace_content,
    ])
    return "\n".join(parts)


def _build_review_request(config: dict, project_dir: str, input_text: str) -> GenericAIReviewRequest:
    """将项目配置转换为通用 AI 评审请求。"""
    ai_cfg = config.get("ai_review", {})
    workspace = config.get("workspace", {})
    sandbox_res = ai_cfg.get("sandbox_resources", {})

    prompt_file = Path(project_dir) / ai_cfg.get("prompt_file", "prompt.md")
    schema_file = Path(project_dir) / ai_cfg.get("schema_file", "schema.json")
    if not prompt_file.is_file():
        raise FileNotFoundError(f"prompt 文件不存在: {prompt_file}")
    if not schema_file.is_file():
        raise FileNotFoundError(f"schema 文件不存在: {schema_file}")

    result_path = os.environ.get(
        "AI_REVIEW_RESULT_PATH",
        workspace.get("ai_review_result_path", "/workspace/ai_review_result.json"),
    )
    error_path = os.environ.get(
        "AI_REVIEW_ERROR_PATH",
        workspace.get("ai_review_error_path", DEFAULT_ERROR_PATH),
    )

    model = (
        os.environ.get("AI_REVIEW_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or ai_cfg.get("model")
        or DEFAULT_MODEL
    )
    timeout = int(
        os.environ.get("AI_REVIEW_TIMEOUT")
        or os.environ.get("CLAUDE_TIMEOUT")
        or ai_cfg.get("timeout", 600)
    )
    mode = (os.environ.get("AI_REVIEW_MODE") or "auto").strip().lower()
    openrouter_base_url = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    return GenericAIReviewRequest(
        prompt_text=prompt_file.read_text(encoding="utf-8"),
        schema_text=schema_file.read_text(encoding="utf-8"),
        input_text=input_text,
        output_path=result_path,
        error_output_path=error_path,
        model=model,
        mode=mode,
        timeout=timeout,
        openrouter_base_url=openrouter_base_url,
        daytona_snapshot=os.environ.get(
            "SNAPSHOT_NAME", ai_cfg.get("sandbox_snapshot", "daytona-medium")
        ),
        daytona_cpu=int(sandbox_res.get("cpu", 2)),
        daytona_memory=int(sandbox_res.get("memory", 2)),
        daytona_disk=int(sandbox_res.get("disk", 5)),
    )


def _write_wrapper_error(error_path: str, message: str) -> None:
    """在通用节点还未被调用前的异常，写入统一错误文件。"""
    output_dir = os.path.dirname(error_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(error_path, "w", encoding="utf-8") as f:
        json.dump(
            {"success": False, "error": message, "error_type": "ExpertReviewWrapperError"},
            f,
            ensure_ascii=False,
            indent=2,
        )


def run_ai_review(record_id: str, project_dir: str) -> int:
    """执行 expert_review 项目的 AI 评审包装流程。"""
    config = load_project_config(project_dir)
    client = FeishuClient.from_config(config)
    feishu = config["feishu"]
    app_token = feishu["app_token"]
    table_id = feishu["table_id"]
    workspace = config.get("workspace", {})

    trace_input_path = os.environ.get(
        "TRACE_OUTPUT_PATH",
        workspace.get("trace_path", "/workspace/trace.jsonl"),
    )

    print("===== AI 评审开始 =====")
    print(f"Record ID (主表): {record_id}")

    print("\n--- 从主表获取记录 ---")
    record = client.get_record(app_token, table_id, record_id)
    fields = record.get("fields", {})

    if not os.path.exists(trace_input_path):
        print("\n--- Trace 文件不存在，重新下载 ---")
        trace_field_name = get_field_name(config, "trace_file")
        trace_field = fields.get(trace_field_name)
        file_tokens = extract_attachment_file_tokens(trace_field)
        if file_tokens:
            output_dir = os.path.dirname(trace_input_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            bundle = download_and_merge_trace_attachments(
                client, trace_field, trace_input_path
            )
            print(f"Trace 附件数量: {bundle.attachment_count}")
            print(f"Trace 附件列表: {', '.join(bundle.attachment_names)}")
            print(f"Trace 合并文件: {trace_input_path} ({bundle.total_bytes} 字节)")
        else:
            print("警告: 主表中未找到 Trace 附件", file=sys.stderr)

    print("\n--- 提取 Trace 用户聚焦内容 ---")
    trace_content = extract_user_focused_content(trace_input_path, max_bytes=200000)
    print(f"Trace 精简内容长度: {len(trace_content)} 字符")

    input_text = _build_input_text(fields, trace_content, config)
    print(f"输入文本总长度: {len(input_text)} 字符")

    request = _build_review_request(config, project_dir, input_text)
    outcome = run_generic_ai_review(request)
    if not outcome.success:
        print(f"AI 评审失败: {outcome.error}", file=sys.stderr)
        print(f"错误结果已保存: {request.error_output_path}")
        # 清理旧的成功结果文件，防止 writeback 读到上次残留的脏数据
        if os.path.exists(request.output_path):
            os.remove(request.output_path)
            print(f"已清理旧的评审结果文件: {request.output_path}")
        return 1

    print(f"AI 评审结果已保存: {request.output_path}")
    print(f"模式: {outcome.mode_used}, 耗时: {outcome.elapsed_seconds:.1f}s")
    if outcome.result_json is not None:
        preview = json.dumps(outcome.result_json, ensure_ascii=False, indent=2)[:1000]
        print(f"结果内容:\n{preview}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物 AI 评审")
    parser.add_argument("--record-id", required=True, help="主表 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    args = parser.parse_args()

    error_path = os.environ.get("AI_REVIEW_ERROR_PATH", DEFAULT_ERROR_PATH)
    try:
        exit_code = run_ai_review(args.record_id, args.project_dir)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        _write_wrapper_error(error_path, str(e))
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
