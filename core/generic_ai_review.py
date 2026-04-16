#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
通用 AI 评审执行器。

职责：
1. 解析 CLI / 环境变量输入
2. 统一处理 prompt / schema / input 三类来源
3. 选择 AI 调用通道（local Claude CLI / Daytona / direct API）
4. 规范化并校验结构化输出
5. 将成功结果写入 output_path，将失败信息写入 error_output_path

注意：该模块只负责“结构化 AI 评审”，不负责业务取数、业务输入拼装、回填外部系统。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"
DEFAULT_TIMEOUT = 600
DEFAULT_OUTPUT_PATH = "/workspace/ai_review_result.json"
DEFAULT_ERROR_PATH = "/workspace/ai_review_error.json"
DEFAULT_MODE = "auto"
VALID_MODES = {"auto", "local_cli", "daytona", "api"}


class GenericAIReviewError(RuntimeError):
    """通用 AI 评审基类异常。"""


class GenericAIReviewConfigError(GenericAIReviewError):
    """配置解析异常。"""


class GenericAIReviewSchemaError(GenericAIReviewError):
    """Schema 解析或校验异常。"""


class GenericAIReviewExecutionError(GenericAIReviewError):
    """模型执行异常。"""


@dataclass
class GenericAIReviewRequest:
    """执行一次通用 AI 评审所需的全部参数。"""

    prompt_text: str
    schema_text: str
    input_text: str
    output_path: str
    error_output_path: str
    model: str = DEFAULT_MODEL
    mode: str = DEFAULT_MODE
    timeout: int = DEFAULT_TIMEOUT
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    daytona_snapshot: str = "daytona-medium"
    daytona_cpu: int = 2
    daytona_memory: int = 4
    daytona_disk: int = 5


@dataclass
class GenericAIReviewOutcome:
    """通用 AI 评审执行结果。"""

    success: bool
    result_json: dict[str, Any] | None = None
    error: str = ""
    error_type: str = ""
    raw_output: str = ""
    mode_used: str = ""
    elapsed_seconds: float = 0.0


def parse_cli_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数。"""
    parser = argparse.ArgumentParser(description="通用 AI 评审节点")
    parser.add_argument("--prompt-file", help="prompt 文件路径")
    parser.add_argument("--prompt-text", help="prompt 文本内容")
    parser.add_argument("--schema-file", help="schema 文件路径")
    parser.add_argument("--schema-text", help="schema 文本内容")
    parser.add_argument("--input-file", help="待评审输入文件路径")
    parser.add_argument("--input-text", help="待评审输入文本")
    parser.add_argument("--output-path", help="成功结果输出路径")
    parser.add_argument("--error-output-path", help="失败结果输出路径")
    parser.add_argument("--model", help="模型名")
    parser.add_argument("--mode", choices=sorted(VALID_MODES), help="执行模式")
    parser.add_argument("--timeout", type=int, help="超时时间（秒）")
    return parser.parse_args(argv)


def resolve_request_from_sources(
    args: argparse.Namespace,
    env: Mapping[str, str] | None = None,
    *,
    model_default: str = DEFAULT_MODEL,
    timeout_default: int = DEFAULT_TIMEOUT,
    output_default: str = DEFAULT_OUTPUT_PATH,
    error_default: str = DEFAULT_ERROR_PATH,
    daytona_snapshot: str = "daytona-medium",
    daytona_cpu: int = 2,
    daytona_memory: int = 4,
    daytona_disk: int = 5,
    openrouter_base_url: str | None = None,
) -> GenericAIReviewRequest:
    """从 CLI + 环境变量构造执行请求。"""
    env_map = dict(env or os.environ)

    prompt_text = _resolve_text_source(
        label="prompt",
        cli_file=getattr(args, "prompt_file", None),
        cli_text=getattr(args, "prompt_text", None),
        env_file=env_map.get("AI_REVIEW_PROMPT_FILE"),
        env_text=_read_env_text(env_map, "AI_REVIEW_PROMPT"),
    )
    schema_text = _resolve_text_source(
        label="schema",
        cli_file=getattr(args, "schema_file", None),
        cli_text=getattr(args, "schema_text", None),
        env_file=env_map.get("AI_REVIEW_SCHEMA_FILE"),
        env_text=_read_env_text(env_map, "AI_REVIEW_SCHEMA"),
    )
    input_text = _resolve_text_source(
        label="input",
        cli_file=getattr(args, "input_file", None),
        cli_text=getattr(args, "input_text", None),
        env_file=env_map.get("AI_REVIEW_INPUT_FILE"),
        env_text=_read_env_text(env_map, "AI_REVIEW_INPUT"),
    )

    output_path = (
        getattr(args, "output_path", None)
        or env_map.get("AI_REVIEW_OUTPUT_PATH")
        or env_map.get("AI_REVIEW_RESULT_PATH")
        or output_default
    )
    error_output_path = (
        getattr(args, "error_output_path", None)
        or env_map.get("AI_REVIEW_ERROR_PATH")
        or error_default
    )

    if not output_path:
        raise GenericAIReviewConfigError("缺少 output_path")
    if not error_output_path:
        raise GenericAIReviewConfigError("缺少 error_output_path")
    if os.path.abspath(output_path) == os.path.abspath(error_output_path):
        raise GenericAIReviewConfigError("output_path 与 error_output_path 不能相同")

    model = (
        getattr(args, "model", None)
        or env_map.get("AI_REVIEW_MODEL")
        or env_map.get("ANTHROPIC_MODEL")
        or env_map.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or model_default
    )
    if not model:
        raise GenericAIReviewConfigError("缺少模型配置")

    mode = (
        getattr(args, "mode", None)
        or env_map.get("AI_REVIEW_MODE")
        or DEFAULT_MODE
    ).strip().lower()
    if mode not in VALID_MODES:
        raise GenericAIReviewConfigError(
            f"mode 仅支持 {', '.join(sorted(VALID_MODES))}，收到: {mode}"
        )

    timeout_raw = (
        getattr(args, "timeout", None)
        if getattr(args, "timeout", None) is not None
        else env_map.get("AI_REVIEW_TIMEOUT")
        or env_map.get("CLAUDE_TIMEOUT")
        or timeout_default
    )
    timeout = _parse_positive_int(timeout_raw, "timeout")

    resolved_base_url = openrouter_base_url or env_map.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    return GenericAIReviewRequest(
        prompt_text=prompt_text,
        schema_text=schema_text,
        input_text=input_text,
        output_path=output_path,
        error_output_path=error_output_path,
        model=model,
        mode=mode,
        timeout=timeout,
        openrouter_base_url=resolved_base_url,
        daytona_snapshot=env_map.get("SNAPSHOT_NAME", daytona_snapshot),
        daytona_cpu=daytona_cpu,
        daytona_memory=daytona_memory,
        daytona_disk=daytona_disk,
    )


def resolve_error_output_path(
    args: argparse.Namespace | None = None,
    env: Mapping[str, str] | None = None,
    *,
    default: str = DEFAULT_ERROR_PATH,
) -> str:
    """在请求构造失败时尽量解析错误输出路径。"""
    env_map = dict(env or os.environ)
    if args is not None and getattr(args, "error_output_path", None):
        return args.error_output_path
    return env_map.get("AI_REVIEW_ERROR_PATH") or default


def normalize_schema_payload(schema_text: str) -> dict[str, Any]:
    """兼容包装 schema 与原始 JSON Schema。"""
    try:
        schema_obj = json.loads(schema_text)
    except json.JSONDecodeError as exc:
        raise GenericAIReviewSchemaError(f"schema 不是合法 JSON: {exc}") from exc

    if not isinstance(schema_obj, dict):
        raise GenericAIReviewSchemaError("schema 顶层必须是 object")

    wrapped_schema = schema_obj.get("schema")
    if isinstance(wrapped_schema, dict):
        normalized = {
            "name": schema_obj.get("name") or schema_obj.get("title") or "ai_review_result",
            "strict": bool(schema_obj.get("strict", True)),
            "schema": wrapped_schema,
        }
    elif isinstance(schema_obj.get("parameters"), dict):
        normalized = {
            "name": schema_obj.get("name") or schema_obj.get("title") or "ai_review_result",
            "strict": bool(schema_obj.get("strict", True)),
            "schema": schema_obj["parameters"],
        }
    else:
        normalized = {
            "name": schema_obj.get("title") or "ai_review_result",
            "strict": True,
            "schema": schema_obj,
        }

    if not isinstance(normalized["schema"], dict):
        raise GenericAIReviewSchemaError("schema.schema 必须是 object")
    return normalized


def unwrap_schema_envelope(result_obj: Any, schema_payload: Mapping[str, Any]) -> dict[str, Any]:
    """解开可能被 schema name 或 CLI 执行器包裹的多层结果。

    处理的包装格式:
    1. {"expert_review_result": {实际结果}}  — schema name 包装
    2. {"type":"result","result":"{...}"}    — Claude CLI --output-format json
    3. {"structured_output": {实际结果}}     — 某些执行器格式
    4. {"result": {实际结果}}               — 通用 result 包装
    """
    if not isinstance(result_obj, dict):
        raise GenericAIReviewSchemaError("模型输出必须是 JSON object")

    root_props = schema_payload.get("schema", {}).get("properties", {})
    root_prop_keys = set(root_props.keys()) if isinstance(root_props, dict) else set()
    schema_name = schema_payload.get("name", "")

    # 递归解包，最多尝试 5 层防止无限循环
    for _ in range(5):
        # 已经是目标结构，直接返回
        if root_prop_keys and root_prop_keys.issubset(set(result_obj.keys())):
            break

        unwrapped = False

        # 尝试 schema name 包装: {"expert_review_result": {...}}
        if schema_name:
            inner = result_obj.get(schema_name)
            if isinstance(inner, dict):
                result_obj = inner
                unwrapped = True
                continue

        # 尝试 CLI/执行器包装: {"result": "..."} 或 {"result": {...}}
        for wrapper_key in ("structured_output", "result"):
            val = result_obj.get(wrapper_key)
            if isinstance(val, dict) and val:
                result_obj = val
                unwrapped = True
                break
            if isinstance(val, str) and val.strip():
                try:
                    parsed = json.loads(val.strip())
                    if isinstance(parsed, dict):
                        result_obj = parsed
                        unwrapped = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass

        if not unwrapped:
            break

    # 递归清理 schema 中未声明的多余字段
    _strip_extra_fields(result_obj, schema_payload.get("schema", {}))

    return result_obj


def _strip_extra_fields(obj: Any, schema: Mapping[str, Any]) -> None:
    """递归删除 additionalProperties:false 的 object 中未声明的字段。"""
    if not isinstance(obj, dict) or not isinstance(schema, Mapping):
        return
    if schema.get("type") != "object":
        return
    props = schema.get("properties")
    if not isinstance(props, Mapping):
        return
    if schema.get("additionalProperties") is False:
        for key in list(obj.keys()):
            if key not in props:
                del obj[key]
    for key, sub_schema in props.items():
        if key in obj and isinstance(sub_schema, Mapping):
            _strip_extra_fields(obj[key], sub_schema)


def _auto_fill_totals(result_obj: dict[str, Any], schema_payload: Mapping[str, Any]) -> None:
    """自动补算缺失的 total 字段。

    当模块（如 expert_ability / trace_asset）的 schema 声明了 total 为 required，
    但模型输出中缺少 total 时，根据各维度 score 自动求和补上。
    """
    root_schema = schema_payload.get("schema", {})
    root_props = root_schema.get("properties", {})
    if not isinstance(root_props, Mapping):
        return

    for prop_key, prop_schema in root_props.items():
        if not isinstance(prop_schema, Mapping) or prop_schema.get("type") != "object":
            continue
        module_data = result_obj.get(prop_key)
        if not isinstance(module_data, dict):
            continue
        # 仅在 total 是 required 但缺失时补算
        module_required = prop_schema.get("required", [])
        module_properties = prop_schema.get("properties", {})
        if "total" not in module_required or "total" in module_data:
            continue
        if "total" not in module_properties:
            continue
        # 求和所有维度的 score
        total = 0
        for dim_key, dim_schema in module_properties.items():
            if dim_key == "total":
                continue
            if not isinstance(dim_schema, Mapping) or dim_schema.get("type") != "object":
                continue
            dim_data = module_data.get(dim_key)
            if isinstance(dim_data, dict):
                score = dim_data.get("score")
                if isinstance(score, (int, float)):
                    total += int(score)
        module_data["total"] = total


def validate_result_against_schema(
    result_obj: dict[str, Any],
    schema_payload: Mapping[str, Any],
) -> None:
    """校验结构化结果与 JSON Schema 一致。"""
    schema = schema_payload.get("schema", {})
    try:
        import jsonschema  # type: ignore
    except ImportError:
        _fallback_validate_schema(result_obj, schema, path="$")
        return

    try:
        jsonschema.validate(instance=result_obj, schema=schema)
    except jsonschema.ValidationError as exc:  # type: ignore[attr-defined]
        raise GenericAIReviewSchemaError(f"模型输出不符合 schema: {exc.message}") from exc


def run_generic_ai_review(request: GenericAIReviewRequest) -> GenericAIReviewOutcome:
    """执行通用 AI 评审，并负责结果/错误落盘。"""
    try:
        schema_payload = normalize_schema_payload(request.schema_text)
        outcome = _execute_ai_review(request, schema_payload)
        result_obj = unwrap_schema_envelope(outcome.result_json, schema_payload)
        _auto_fill_totals(result_obj, schema_payload)
        validate_result_against_schema(result_obj, schema_payload)
        _write_json_file(request.output_path, result_obj)
        _delete_file_if_exists(request.error_output_path)
        outcome.success = True
        outcome.result_json = result_obj
        return outcome
    except GenericAIReviewError as exc:
        outcome = GenericAIReviewOutcome(
            success=False,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        _write_error_file(request.error_output_path, request, outcome)
        return outcome
    except Exception as exc:  # pragma: no cover - 防御性兜底
        outcome = GenericAIReviewOutcome(
            success=False,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        _write_error_file(request.error_output_path, request, outcome)
        return outcome


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    args = parse_cli_args(argv)
    env_map = dict(os.environ)
    try:
        request = resolve_request_from_sources(args, env_map)
    except Exception as exc:
        error_path = resolve_error_output_path(args, env_map)
        fallback_request = GenericAIReviewRequest(
            prompt_text="",
            schema_text="{}",
            input_text="",
            output_path=env_map.get("AI_REVIEW_OUTPUT_PATH", DEFAULT_OUTPUT_PATH),
            error_output_path=error_path,
        )
        outcome = GenericAIReviewOutcome(
            success=False,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        _write_error_file(error_path, fallback_request, outcome)
        print(f"错误: {exc}")
        return 1

    outcome = run_generic_ai_review(request)
    if outcome.success:
        print(f"AI 评审成功，模式: {outcome.mode_used}，耗时: {outcome.elapsed_seconds:.1f}s")
        print(f"结果已保存: {request.output_path}")
        return 0

    print(f"AI 评审失败: {outcome.error}")
    print(f"错误结果已保存: {request.error_output_path}")
    return 1


def _execute_ai_review(
    request: GenericAIReviewRequest,
    schema_payload: Mapping[str, Any],
) -> GenericAIReviewOutcome:
    """按模式执行 AI 调用。"""
    attempts: list[tuple[str, Any]] = []
    has_claude_cli = shutil.which("claude") is not None
    can_daytona = bool(os.environ.get("DAYTONA_API_KEY"))
    can_api = bool(os.environ.get("OPENROUTER_API_KEY"))

    if request.mode == "auto":
        if has_claude_cli:
            attempts.append(("local_cli", _run_local_claude_cli))
        if can_daytona:
            attempts.append(("daytona", _run_daytona))
        if can_api:
            attempts.append(("api", _run_direct_api))
    elif request.mode == "local_cli":
        attempts.append(("local_cli", _run_local_claude_cli))
    elif request.mode == "daytona":
        attempts.append(("daytona", _run_daytona))
    elif request.mode == "api":
        attempts.append(("api", _run_direct_api))

    if not attempts:
        raise GenericAIReviewExecutionError("无可用评审通道")

    errors: list[str] = []
    for mode_name, runner in attempts:
        t0 = time.time()
        try:
            print(f"--- 通用 AI 评审通道: {mode_name} ---")
            raw_result = runner(request, schema_payload)
            elapsed = time.time() - t0
            return GenericAIReviewOutcome(
                success=True,
                result_json=raw_result,
                mode_used=mode_name,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            errors.append(f"{mode_name}: {exc}")
            if request.mode != "auto":
                break

    raise GenericAIReviewExecutionError("；".join(errors))


def _run_local_claude_cli(
    request: GenericAIReviewRequest,
    schema_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """在当前环境直接调用 Claude CLI。"""
    if shutil.which("claude") is None:
        raise GenericAIReviewExecutionError("当前环境未安装 claude CLI")

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = os.path.join(tmpdir, "prompt.md")
        schema_file = os.path.join(tmpdir, "schema.json")
        input_file = os.path.join(tmpdir, "input.txt")

        Path(prompt_file).write_text(request.prompt_text, encoding="utf-8")
        Path(schema_file).write_text(json.dumps(schema_payload, ensure_ascii=False), encoding="utf-8")
        Path(input_file).write_text(request.input_text, encoding="utf-8")

        cmd = (
            f"cat {input_file} | claude -p "
            f"--system-prompt-file {prompt_file} "
            f"--output-format json "
            f'--json-schema "$(cat {schema_file})"'
        )
        env = os.environ.copy()
        env["ANTHROPIC_MODEL"] = request.model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = request.model

        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=request.timeout,
            cwd=tmpdir,
            env=env,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()[:500] if proc.stderr else ""
            raise GenericAIReviewExecutionError(
                f"Claude CLI 退出码 {proc.returncode}: {stderr}"
            )

        raw = proc.stdout.strip()
        if not raw:
            raise GenericAIReviewExecutionError("Claude CLI 返回空输出")

        repaired = _repair_json_text(raw)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise GenericAIReviewExecutionError(f"Claude CLI 输出无法解析为 JSON: {exc}") from exc


def _run_daytona(
    request: GenericAIReviewRequest,
    schema_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """通过 Daytona 沙箱执行。"""
    from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox

    openrouter_base_url = request.openrouter_base_url
    if openrouter_base_url.endswith("/v1"):
        openrouter_base_url = openrouter_base_url[: -len("/v1")]

    run_config = DaytonaRunConfig(
        api_key=os.environ.get("DAYTONA_API_KEY", ""),
        snapshot=request.daytona_snapshot,
        cpu=request.daytona_cpu,
        memory=request.daytona_memory,
        disk=request.daytona_disk,
        openrouter_base_url=openrouter_base_url,
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        model=request.model,
        timeout=request.timeout,
    )
    result = run_claude_in_sandbox(
        run_config,
        request.prompt_text,
        json.dumps(schema_payload, ensure_ascii=False),
        request.input_text,
    )
    if not result.success:
        raise GenericAIReviewExecutionError(result.error or "Daytona 执行失败")
    return result.result_json or {}


def _run_direct_api(
    request: GenericAIReviewRequest,
    schema_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """直连 OpenRouter API。"""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise GenericAIReviewExecutionError("OPENROUTER_API_KEY 未设置")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise GenericAIReviewExecutionError(
            "未安装 openai 依赖，请安装 openai 后再使用 api 模式"
        ) from exc

    base_url = request.openrouter_base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    client = OpenAI(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=request.model,
        messages=[
            {"role": "system", "content": request.prompt_text},
            {"role": "user", "content": request.input_text},
        ],
        response_format={"type": "json_schema", "json_schema": dict(schema_payload)},
        timeout=request.timeout,
    )
    raw = resp.choices[0].message.content
    if not raw:
        raise GenericAIReviewExecutionError("API 返回空内容")
    repaired = _repair_json_text(raw)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        raise GenericAIReviewExecutionError(f"API 输出无法解析为 JSON: {exc}") from exc


def _resolve_text_source(
    *,
    label: str,
    cli_file: str | None,
    cli_text: str | None,
    env_file: str | None,
    env_text: str | None,
) -> str:
    """按 CLI 优先、环境变量兜底的规则解析文本源。"""
    if cli_file and cli_text is not None:
        raise GenericAIReviewConfigError(f"{label} 不能同时提供 --{label}-file 和 --{label}-text")
    if env_file and env_text is not None:
        raise GenericAIReviewConfigError(
            f"{label} 不能同时提供 AI_REVIEW_{label.upper()}_FILE 和 AI_REVIEW_{label.upper()}"
        )

    if cli_file:
        return _read_text_file(cli_file, label)
    if cli_text is not None:
        return cli_text
    if env_file:
        return _read_text_file(env_file, label)
    if env_text is not None:
        return env_text

    raise GenericAIReviewConfigError(f"缺少 {label} 输入")


def _read_text_file(path: str, label: str) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        raise GenericAIReviewConfigError(f"{label} 文件不存在: {path}")
    return file_path.read_text(encoding="utf-8")


def _read_env_text(env_map: Mapping[str, str], key: str) -> str | None:
    return env_map[key] if key in env_map else None


def _parse_positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GenericAIReviewConfigError(f"{label} 必须是正整数，收到: {value}") from exc
    if parsed <= 0:
        raise GenericAIReviewConfigError(f"{label} 必须是正整数，收到: {value}")
    return parsed


def _write_json_file(path: str, payload: Mapping[str, Any]) -> None:
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_error_file(
    path: str,
    request: GenericAIReviewRequest,
    outcome: GenericAIReviewOutcome,
) -> None:
    payload = {
        "success": False,
        "error": outcome.error,
        "error_type": outcome.error_type,
        "mode": request.mode,
        "model": request.model,
        "output_path": request.output_path,
    }
    _write_json_file(path, payload)


def _delete_file_if_exists(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return


def _repair_json_text(raw: str) -> str:
    """清理模型输出中的包装层与 markdown 代码块。"""
    raw = raw.strip()
    if not raw:
        return raw

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            structured = obj.get("structured_output")
            if isinstance(structured, dict):
                return json.dumps(structured, ensure_ascii=False)
            result = obj.get("result")
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False)
            if isinstance(result, str):
                return _repair_json_text(result)
        return raw
    except json.JSONDecodeError:
        pass

    fenced_start = raw.find("```json")
    if fenced_start != -1:
        fenced_end = raw.rfind("```")
        if fenced_end > fenced_start:
            snippet = raw[fenced_start + len("```json"):fenced_end].strip()
            return _repair_json_text(snippet)

    first_brace = raw.find("{")
    last_brace = raw.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        snippet = raw[first_brace:last_brace + 1]
        try:
            json.loads(snippet)
            return snippet
        except json.JSONDecodeError:
            pass

    return raw


def _fallback_validate_schema(instance: Any, schema: Mapping[str, Any], *, path: str) -> None:
    """当 jsonschema 不可用时的最小校验器。"""
    if not isinstance(schema, Mapping):
        return

    if "enum" in schema and instance not in schema["enum"]:
        raise GenericAIReviewSchemaError(f"{path} 不在 enum 允许值中")
    if "const" in schema and instance != schema["const"]:
        raise GenericAIReviewSchemaError(f"{path} 不等于 const 约束")

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        errors = []
        for candidate in schema_type:
            try:
                _fallback_validate_schema(instance, {**schema, "type": candidate}, path=path)
                return
            except GenericAIReviewSchemaError as exc:
                errors.append(str(exc))
        raise GenericAIReviewSchemaError("；".join(errors))

    if schema_type == "object":
        if not isinstance(instance, dict):
            raise GenericAIReviewSchemaError(f"{path} 应为 object")

        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise GenericAIReviewSchemaError(f"{path}.{key} 缺失")

        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, subschema in properties.items():
                if key in instance:
                    _fallback_validate_schema(instance[key], subschema, path=f"{path}.{key}")

        if schema.get("additionalProperties") is False and isinstance(properties, Mapping):
            unknown = set(instance.keys()) - set(properties.keys())
            if unknown:
                raise GenericAIReviewSchemaError(
                    f"{path} 存在未声明字段: {', '.join(sorted(unknown))}"
                )
        return

    if schema_type == "array":
        if not isinstance(instance, list):
            raise GenericAIReviewSchemaError(f"{path} 应为 array")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(instance):
                _fallback_validate_schema(item, item_schema, path=f"{path}[{index}]")
        return

    if schema_type == "string":
        if not isinstance(instance, str):
            raise GenericAIReviewSchemaError(f"{path} 应为 string")
        return

    if schema_type == "integer":
        if not (isinstance(instance, int) and not isinstance(instance, bool)):
            raise GenericAIReviewSchemaError(f"{path} 应为 integer")
        _check_numeric_bounds(instance, schema, path)
        return

    if schema_type == "number":
        if not ((isinstance(instance, int) or isinstance(instance, float)) and not isinstance(instance, bool)):
            raise GenericAIReviewSchemaError(f"{path} 应为 number")
        _check_numeric_bounds(float(instance), schema, path)
        return

    if schema_type == "boolean":
        if not isinstance(instance, bool):
            raise GenericAIReviewSchemaError(f"{path} 应为 boolean")
        return

    if schema_type == "null":
        if instance is not None:
            raise GenericAIReviewSchemaError(f"{path} 应为 null")


def _check_numeric_bounds(value: float, schema: Mapping[str, Any], path: str) -> None:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and value < minimum:
        raise GenericAIReviewSchemaError(f"{path} 小于 minimum={minimum}")
    if maximum is not None and value > maximum:
        raise GenericAIReviewSchemaError(f"{path} 大于 maximum={maximum}")


if __name__ == "__main__":
    raise SystemExit(main())
