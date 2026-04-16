#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI 评审结果校验器。

用于在 writeback 等后续阶段读取 AI 结果前，
确认结果至少具备可打分的最小结构，避免因为模型输出异常导致后续流程误判。
"""

from __future__ import annotations

from typing import Any


def validate_ai_review_result(
    ai_result: Any,
    expert_dims: list[dict[str, Any]],
    trace_dims: list[dict[str, Any]],
) -> tuple[bool, str]:
    """校验 AI 评审结果是否具备最小可用结构。"""
    if not isinstance(ai_result, dict):
        return False, "AI 评审结果不是 JSON object"

    # 仅当 error 字段存在且缺少核心打分字段时才判定为错误；
    # 如果打分字段完整，忽略 error 字段（可能是 CLI 包装层残留）
    has_core_fields = (
        isinstance(ai_result.get("expert_ability"), dict)
        and isinstance(ai_result.get("trace_asset"), dict)
    )
    if ai_result.get("error") and not has_core_fields:
        return False, f"AI 评审返回错误: {ai_result['error']}"

    ok, reason = _validate_module(ai_result, "expert_ability", expert_dims)
    if not ok:
        return False, reason

    ok, reason = _validate_module(ai_result, "trace_asset", trace_dims)
    if not ok:
        return False, reason

    overall = ai_result.get("overall_assessment")
    if not isinstance(overall, str) or not overall.strip():
        return False, "缺少 overall_assessment"

    highlights = ai_result.get("trace_highlights")
    if not isinstance(highlights, list):
        return False, "缺少 trace_highlights"

    return True, ""


def _validate_module(
    ai_result: dict[str, Any],
    module_key: str,
    dimensions: list[dict[str, Any]],
) -> tuple[bool, str]:
    module = ai_result.get(module_key)
    if not isinstance(module, dict):
        return False, f"缺少模块: {module_key}"

    total = 0
    for dim in dimensions:
        key = dim.get("key")
        max_score = dim.get("max_score")
        if not key:
            return False, f"{module_key} 配置存在空维度 key"
        if key not in module:
            return False, f"{module_key}.{key} 缺失"

        score = _extract_score(module.get(key))
        if score is None:
            return False, f"{module_key}.{key}.score 缺失或非数字"
        if isinstance(max_score, (int, float)) and not (0 <= score <= max_score):
            return False, f"{module_key}.{key}.score 超出范围"
        total += int(score)

    module_total = module.get("total")
    if module_total is not None:
        if not isinstance(module_total, (int, float)):
            return False, f"{module_key}.total 不是数字"
        if int(module_total) != total:
            return False, f"{module_key}.total 与维度求和不一致"

    return True, ""


def _extract_score(value: Any) -> int | None:
    if isinstance(value, dict):
        score = value.get("score")
    else:
        score = value

    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    return int(score)
