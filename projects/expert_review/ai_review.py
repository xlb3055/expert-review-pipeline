#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第二层：AI 评审

在 Daytona 沙箱中执行 Claude Code，对专家考核产物进行双模块评分：
- 专家能力分（0-10）
- Trace 资产分（0-12）

数据流：
  - 从主表读取数据（record_id 是主表的 record_id）
  - 用 trace_extractor 提取用户聚焦内容
  - AI 评审结果保存到本地 JSON，供 writeback 阶段使用

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
    extract_attachment_file_token,
    extract_attachment_url,
)
from core.trace_extractor import extract_user_focused_content
from projects.expert_review.result_utils import normalize_ai_result

# Daytona 为可选依赖，没装则只走直连 API 模式
try:
    from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox
    _HAS_DAYTONA = True
except ImportError:
    _HAS_DAYTONA = False


def _build_input_text(fields: dict, trace_content: str, config: dict) -> str:
    """组装 AI 评审的输入文本（使用主表字段映射）。"""
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


def _call_claude_cli(prompt_content: str, schema_content: str,
                     input_text: str, model: str, timeout: int = 600) -> dict:
    """在当前环境直接调用 claude CLI（适用于已在 Daytona 沙箱内的场景）。"""
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

        result = normalize_ai_result(raw)
        if not result:
            raise RuntimeError("Claude CLI 返回内容无法解析为评审结果")
        return result


def _call_api_direct(prompt_content: str, schema_content: str,
                     input_text: str, model: str) -> dict:
    """直连 OpenRouter API 完成评审，返回解析后的 JSON dict。"""
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


def run_ai_review(record_id: str, project_dir: str) -> int:
    """
    执行 AI 评审流程。

    record_id: 主表的 record_id
    返回: 0=成功, 1=失败
    """
    config = load_project_config(project_dir)
    client = FeishuClient.from_config(config)
    feishu = config["feishu"]
    app_token = feishu["app_token"]
    table_id = feishu["table_id"]
    ai_cfg = config.get("ai_review", {})
    workspace = config.get("workspace", {})

    trace_input_path = os.environ.get(
        "TRACE_OUTPUT_PATH",
        workspace.get("trace_path", "/workspace/trace.jsonl"),
    )
    result_path = os.environ.get(
        "AI_REVIEW_RESULT_PATH",
        workspace.get("ai_review_result_path", "/workspace/ai_review_result.json"),
    )

    model = os.environ.get(
        "ANTHROPIC_MODEL",
        os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", ai_cfg.get("model", "") or "anthropic/claude-sonnet-4-6"),
    )

    t0 = time.time()
    print("===== AI 评审开始 =====")
    print(f"Record ID (主表): {record_id}")
    print(f"模型: {model}")

    # 1. 从主表获取记录
    print("\n--- 从主表获取记录 ---")
    record = client.get_record(app_token, table_id, record_id)
    fields = record.get("fields", {})

    # 2. 如果 Trace 文件不存在（pre_screen 已下载），需要重新下载
    if not os.path.exists(trace_input_path):
        print("\n--- Trace 文件不存在，重新下载 ---")
        trace_field_name = get_field_name(config, "trace_file")
        trace_field = fields.get(trace_field_name)
        file_token = extract_attachment_file_token(trace_field)
        if file_token:
            output_dir = os.path.dirname(trace_input_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            download_url = extract_attachment_url(trace_field)
            client.download_attachment(file_token, trace_input_path,
                                       download_url=download_url or None)
        else:
            print("警告: 主表中未找到 Trace 附件", file=sys.stderr)

    # 3. 用 trace_extractor 提取用户聚焦内容
    print("\n--- 提取 Trace 用户聚焦内容 ---")
    trace_content = extract_user_focused_content(trace_input_path, max_bytes=200000)
    print(f"Trace 精简内容长度: {len(trace_content)} 字符")

    # 4. 组装输入文本
    input_text = _build_input_text(fields, trace_content, config)
    print(f"输入文本总长度: {len(input_text)} 字符")

    # 5. 读取 prompt 和 schema
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

    # 6. 调用 AI 评审
    # 优先级: 本地 Claude CLI（已在沙箱内） > 新建 Daytona 沙箱 > 直连 API
    # AI_REVIEW_MODE=api 可强制跳过前两者
    import shutil
    mode = os.environ.get("AI_REVIEW_MODE", "").lower()
    has_claude_cli = shutil.which("claude") is not None
    can_daytona = _HAS_DAYTONA and os.environ.get("DAYTONA_API_KEY", "")
    can_api = bool(os.environ.get("OPENROUTER_API_KEY", ""))
    result_obj = None
    elapsed = 0
    ai_timeout = int(os.environ.get("CLAUDE_TIMEOUT", str(ai_cfg.get("timeout", 600))))

    # 6a. 本地有 claude CLI → 直接跑（已在 Daytona 沙箱内，免费）
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

    # 6b. 本地没有 CLI，尝试新建 Daytona 沙箱
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

    # 6c. 兜底：直连 API
    if result_obj is None and can_api:
        if has_claude_cli or can_daytona:
            print("自动回退直连 API ...")
        print(f"\n--- 直连 API --- [{time.time()-t0:.1f}s]")
        result_obj = _call_api_direct(prompt_content, schema_content, input_text, model)
        elapsed = time.time() - t0

    if result_obj is None:
        print("错误: 无可用的 AI 评审通道", file=sys.stderr)
        _save_error_result("无可用评审通道", result_path)
        return 1

    # 7. 统一解包执行器/模型包装
    raw_keys = list(result_obj.keys()) if isinstance(result_obj, dict) else []
    raw_preview = (
        json.dumps(result_obj, ensure_ascii=False, indent=2)[:2000]
        if isinstance(result_obj, (dict, list))
        else str(result_obj)[:2000]
    )
    result_obj = normalize_ai_result(result_obj)
    if not isinstance(result_obj, dict) or not result_obj:
        if raw_keys:
            print(f"AI 评审原始结果键: {raw_keys}", file=sys.stderr)
        print(f"AI 评审原始结果预览:\n{raw_preview}", file=sys.stderr)
        print("错误: AI 评审结果为空或无法解包", file=sys.stderr)
        _save_error_result("AI 评审结果为空或无法解包", result_path)
        return 1

    # 8. 保存结果
    result_dir = os.path.dirname(result_path)
    if result_dir:
        os.makedirs(result_dir, exist_ok=True)
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result_obj, f, ensure_ascii=False, indent=2)
    print(f"\nAI 评审结果已保存: {result_path}")
    if raw_keys:
        print(f"原始结果键: {raw_keys}")
    print(f"解包后结果键: {list(result_obj.keys())}")
    print(f"结果内容:\n{json.dumps(result_obj, ensure_ascii=False, indent=2)[:1000]}")
    print(f"总耗时: {elapsed:.1f}s")

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
    parser.add_argument("--record-id", required=True, help="主表 record_id")
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
