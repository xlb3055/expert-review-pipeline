#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
AI 评审结果结构校验。

用于在 ai_review 保存前、writeback 回填前，确认结果至少具备可打分的最小结构，
避免因为结果缺失、结构异常或错误占位结果，导致后续被当作 0 分正常回填。
"""

from typing import Any


def _extract_score_value(value: Any):
    """从维度值中提取 score。支持 {score: n} 或直接数值。"""
    if isinstance(value, dict):
        score = value.get("score")
        if isinstance(score, (int, float)):
            return score
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _validate_module(ai_result: dict, module_key: str, dimensions: list[dict]) -> list[str]:
    """校验单个评分模块。"""
    errors = []
    module_data = ai_result.get(module_key)

    if not isinstance(module_data, dict):
        return [f"{module_key} 缺失或不是对象"]

    for dim in dimensions:
        key = dim.get("key")
        if not key:
            continue
        if key not in module_data:
            errors.append(f"{module_key}.{key} 缺失")
            continue
        if _extract_score_value(module_data.get(key)) is None:
            errors.append(f"{module_key}.{key}.score 缺失或不是数值")

    total = module_data.get("total")
    if not isinstance(total, (int, float)):
        errors.append(f"{module_key}.total 缺失或不是数值")

    return errors


def validate_ai_review_result(ai_result: Any, expert_dims: list[dict],
                              trace_dims: list[dict]) -> tuple[bool, str]:
    """
    校验 AI 评审结果是否可用于 writeback 打分。

    返回:
      (True, "") 表示结构可用
      (False, reason) 表示结果缺失/错误/结构异常
    """
    if not isinstance(ai_result, dict):
        return False, "顶层结果不是 JSON 对象"

    if ai_result.get("error"):
        return False, f"AI 评审失败: {ai_result['error']}"

    errors = []
    errors.extend(_validate_module(ai_result, "expert_ability", expert_dims))
    errors.extend(_validate_module(ai_result, "trace_asset", trace_dims))

    if not isinstance(ai_result.get("overall_assessment"), str):
        errors.append("overall_assessment 缺失或不是字符串")

    if not isinstance(ai_result.get("trace_highlights"), list):
        errors.append("trace_highlights 缺失或不是数组")

    if errors:
        return False, "; ".join(errors)

    return True, ""
