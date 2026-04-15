#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
内容审核 AI 评审脚本（示例）

从 ctx_data.json 读取内容，调用 AI 评审，结果写回 ctx_data.json。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core.ctx_utils import load_ctx_data, save_ctx_data
from core.config_loader import load_project_config


def _call_api_direct(prompt_content: str, schema_content: str,
                     input_text: str, model: str) -> dict:
    """直连 OpenRouter API 完成评审。"""
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    client = OpenAI(base_url=base_url, api_key=api_key)

    schema_obj = json.loads(schema_content)
    json_schema = {
        "name": schema_obj.get("name", "content_review_result"),
        "strict": schema_obj.get("strict", True),
        "schema": schema_obj.get("schema", schema_obj.get("parameters", schema_obj)),
    }

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt_content},
            {"role": "user", "content": input_text},
        ],
        response_format={"type": "json_schema", "json_schema": json_schema},
        timeout=300,
    )

    raw = resp.choices[0].message.content
    return json.loads(raw)


def run_ai_review(record_id: str, project_dir: str, ctx_data_file: str) -> int:
    config = load_project_config(project_dir)
    ai_cfg = config.get("ai_review", {})

    ctx_data = load_ctx_data(ctx_data_file)

    model = os.environ.get(
        "ANTHROPIC_MODEL",
        ai_cfg.get("model", "") or "anthropic/claude-sonnet-4-6",
    )

    # 组装输入
    input_text = (
        f"# 内容审核输入\n\n"
        f"## 标题\n{ctx_data.get('title', '')}\n\n"
        f"## 作者\n{ctx_data.get('author', '')}\n\n"
        f"## 内容正文\n{ctx_data.get('content', '')}"
    )

    prompt_file = Path(project_dir) / ai_cfg.get("prompt_file", "prompt.md")
    schema_file = Path(project_dir) / ai_cfg.get("schema_file", "schema.json")

    if not prompt_file.is_file() or not schema_file.is_file():
        print("错误: prompt.md 或 schema.json 不存在", file=sys.stderr)
        return 1

    prompt_content = prompt_file.read_text(encoding="utf-8")
    schema_content = schema_file.read_text(encoding="utf-8")

    can_api = bool(os.environ.get("OPENROUTER_API_KEY", ""))
    if not can_api:
        print("错误: 未设置 OPENROUTER_API_KEY", file=sys.stderr)
        return 1

    result_obj = _call_api_direct(prompt_content, schema_content, input_text, model)

    ctx_data["ai_review_result"] = result_obj
    # 简单映射：从 AI 结果提取 review_status 和 review_note
    ctx_data["review_status"] = result_obj.get("conclusion", "pass")
    ctx_data["review_note"] = result_obj.get("note", "")

    save_ctx_data(ctx_data_file, ctx_data)
    print("AI 审核结果已写回 ctx_data.json")
    return 0


def main():
    parser = argparse.ArgumentParser(description="内容审核 AI 评审")
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--ctx-data-file", required=True)
    args = parser.parse_args()

    try:
        exit_code = run_ai_review(args.record_id, args.project_dir, args.ctx_data_file)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
