#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第二层：AI 评审

从 ctx_data.json 读取字段和 trace_content，调用 AI 评审。
结果写回 ctx_data.json。

用法:
  python3 ai_review.py --record-id <id> --project-dir <dir> --ctx-data-file <path>
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core.ctx_utils import load_ctx_data, save_ctx_data
from core.config_loader import load_project_config
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_link_url,
    extract_attachment_file_token,
    extract_attachment_url,
)
from core.trace_extractor import extract_user_focused_content

# Daytona 为可选依赖
try:
    from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox
    _HAS_DAYTONA = True
except ImportError:
    _HAS_DAYTONA = False


def _build_input_text(ctx_data: dict, trace_content: str) -> str:
    """组装 AI 评审的输入文本（从 ctx_data 读取字段）。"""
    task_desc = ctx_data.get("task_description", "")
    expert_name = ctx_data.get("expert_name", "")
    expert_id = ctx_data.get("expert_id", "")
    position = ctx_data.get("position", "")

    # 最终产物：优先链接，回退文本
    raw_product = ctx_data.get("_raw_final_product")
    product_link = extract_link_url(raw_product) if raw_product else ""
    if not product_link:
        product_link = ctx_data.get("final_product", "")

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


def _call_claude_cli(prompt_content: str, schema_content: str,
                     input_text: str, model: str, timeout: int = 600) -> dict:
    """在当前环境直接调用 claude CLI。"""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        schema_file = os.path.join(tmpdir, "schema.json")
        input_file = os.path.join(tmpdir, "input.txt")

        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt_content)
        with open(schema_file, "w", encoding="utf-8") as f:
            f.write(schema_content)
        with open(input_file, "w", encoding="utf-8") as f:
            f.write(input_text)

        cmd = (
            f"cat {input_file} | claude -p "
            f"--system-prompt-file {prompt_file} "
            f"--output-format json "
            f'--json-schema "$(cat {schema_file})"'
        )

        print(f"执行 Claude CLI: claude -p ...")
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=tmpdir,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.strip()[:500] if proc.stderr else ""
            raise RuntimeError(f"Claude CLI 退出码 {proc.returncode}: {stderr}")

        raw = proc.stdout.strip()
        if not raw:
            raise RuntimeError("Claude CLI 返回空输出")

        result = json.loads(raw)
        if isinstance(result.get("result"), str):
            try:
                result = json.loads(result["result"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result


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
        "name": schema_obj.get("name", "expert_review_result"),
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
    """
    执行 AI 评审流程。

    从 ctx_data.json 读取字段和 trace_content，调用 AI 评审。
    结果写回 ctx_data.json。

    返回: 0=成功, 1=失败
    """
    config = load_project_config(project_dir)
    ai_cfg = config.get("ai_review", {})
    workspace = config.get("workspace", {})

    # 从 ctx_data.json 读取数据
    ctx_data = load_ctx_data(ctx_data_file)

    # 粗筛已拒绝 → 跳过 AI 评审
    pre_screen_result = ctx_data.get("pre_screen_result", {})
    if pre_screen_result.get("粗筛状态") == "拒绝":
        print("粗筛已拒绝，跳过 AI 评审")
        ctx_data["ai_review_result"] = _make_error_result("粗筛已拒绝，跳过 AI 评审")
        save_ctx_data(ctx_data_file, ctx_data)
        return 0

    model = os.environ.get(
        "ANTHROPIC_MODEL",
        os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ai_cfg.get("model", "") or "anthropic/claude-sonnet-4-6"),
    )

    t0 = time.time()
    print("===== AI 评审开始 =====")
    print(f"Record ID: {record_id}")
    print(f"模型: {model}")

    # 获取 trace 内容
    trace_content = ctx_data.get("trace_content", "")

    # 如果 pre_screen 没有提取 trace_content，尝试从文件读取
    if not trace_content:
        trace_path = ctx_data.get("trace_output_path", "")
        if not trace_path:
            trace_path = os.environ.get(
                "TRACE_OUTPUT_PATH",
                workspace.get("trace_path", os.path.join(os.path.dirname(ctx_data_file), "trace.jsonl")),
            )
        if os.path.exists(trace_path):
            trace_content = extract_user_focused_content(trace_path, max_bytes=200000)
        else:
            # 需要下载 trace
            print("\n--- Trace 文件不存在，尝试下载 ---")
            raw_trace_file = ctx_data.get("_raw_trace_file")
            file_token = extract_attachment_file_token(raw_trace_file)
            if file_token:
                client = FeishuClient.from_config(config)
                output_dir = os.path.dirname(trace_path)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                download_url = extract_attachment_url(raw_trace_file)
                client.download_attachment(file_token, trace_path,
                                           download_url=download_url or None)
                trace_content = extract_user_focused_content(trace_path, max_bytes=200000)
            else:
                print("警告: 无法获取 Trace 内容", file=sys.stderr)

    print(f"Trace 精简内容长度: {len(trace_content)} 字符")

    # 组装输入文本
    input_text = _build_input_text(ctx_data, trace_content)
    print(f"输入文本总长度: {len(input_text)} 字符")

    # 读取 prompt 和 schema
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

    # 调用 AI 评审
    import shutil
    mode = os.environ.get("AI_REVIEW_MODE", "").lower()
    has_claude_cli = shutil.which("claude") is not None
    can_daytona = _HAS_DAYTONA and os.environ.get("DAYTONA_API_KEY", "")
    can_api = bool(os.environ.get("OPENROUTER_API_KEY", ""))
    result_obj = None
    elapsed = 0
    ai_timeout = int(os.environ.get("CLAUDE_TIMEOUT", str(ai_cfg.get("timeout", 600))))

    # 本地有 claude CLI → 直接跑
    if mode != "api" and has_claude_cli:
        print(f"\n--- 本地 Claude CLI --- [{time.time()-t0:.1f}s]")
        try:
            result_obj = _call_claude_cli(
                prompt_content, schema_content, input_text, model,
                timeout=ai_timeout,
            )
            elapsed = time.time() - t0
        except Exception as e:
            print(f"Claude CLI 失败: {e}", file=sys.stderr)

    # Daytona 沙箱
    if result_obj is None and mode != "api" and can_daytona and not has_claude_cli:
        print(f"\n--- 调用 Daytona 沙箱 --- [{time.time()-t0:.1f}s]")
        sandbox_res = ai_cfg.get("sandbox_resources", {})
        run_config = DaytonaRunConfig(
            api_key=os.environ.get("DAYTONA_API_KEY", ""),
            snapshot=os.environ.get("SNAPSHOT_NAME", ai_cfg.get("sandbox_snapshot", "daytona-medium")),
            cpu=sandbox_res.get("cpu", 2),
            memory=sandbox_res.get("memory", 2),
            disk=sandbox_res.get("disk", 5),
            openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api"),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=model,
            timeout=ai_timeout,
        )
        try:
            result = run_claude_in_sandbox(run_config, prompt_content, schema_content, input_text)
            if result.success:
                result_obj = result.result_json
                elapsed = result.elapsed_seconds
            else:
                print(f"Daytona 沙箱失败: {result.error}", file=sys.stderr)
        except Exception as e:
            print(f"Daytona 沙箱异常: {e}", file=sys.stderr)

    # 兜底：直连 API
    if result_obj is None and can_api:
        if has_claude_cli or can_daytona:
            print("自动回退直连 API ...")
        print(f"\n--- 直连 API --- [{time.time()-t0:.1f}s]")
        result_obj = _call_api_direct(prompt_content, schema_content, input_text, model)
        elapsed = time.time() - t0

    if result_obj is None:
        print("错误: 无可用的 AI 评审通道", file=sys.stderr)
        ctx_data["ai_review_result"] = _make_error_result("无可用评审通道")
        save_ctx_data(ctx_data_file, ctx_data)
        return 1

    # 解包 schema 包装
    if "expert_review_result" in result_obj and "expert_ability" not in result_obj:
        result_obj = result_obj["expert_review_result"]

    # 写回 ctx_data
    ctx_data["ai_review_result"] = result_obj
    save_ctx_data(ctx_data_file, ctx_data)

    print(f"\nAI 评审结果已写回 ctx_data.json")
    print(f"结果内容:\n{json.dumps(result_obj, ensure_ascii=False, indent=2)[:1000]}")
    print(f"总耗时: {elapsed:.1f}s")

    return 0


def _make_error_result(error_msg: str) -> dict:
    """生成错误结果。"""
    return {
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


def main():
    parser = argparse.ArgumentParser(description="专家考核产物 AI 评审")
    parser.add_argument("--record-id", required=True, help="主表 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    parser.add_argument("--ctx-data-file", required=True, help="ctx_data.json 路径")
    args = parser.parse_args()

    try:
        exit_code = run_ai_review(args.record_id, args.project_dir, args.ctx_data_file)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        # 写回错误结果
        try:
            ctx_data = load_ctx_data(args.ctx_data_file)
            ctx_data["ai_review_result"] = _make_error_result(str(e))
            save_ctx_data(args.ctx_data_file, ctx_data)
        except Exception:
            pass
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
