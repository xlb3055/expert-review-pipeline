#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""AI 评审结果解包与归一化工具。"""

import json
import re
from typing import Any


SCORE_ROOT_KEYS = {"expert_ability", "trace_asset"}
WRAPPER_KEYS = ("expert_review_result", "structured_output", "result")


def _looks_like_score_result(obj: Any) -> bool:
    return isinstance(obj, dict) and any(key in obj for key in SCORE_ROOT_KEYS)


def _parse_json_text(text: str) -> Any:
    text = text.strip()
    if not text:
        return None

    candidates = [text]

    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(text[first_brace:last_brace + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def normalize_ai_result(payload: Any, max_depth: int = 8) -> dict:
    """把 Claude/OpenRouter/执行器包装结果归一化为评审结果对象。"""
    current = payload

    for _ in range(max_depth):
        if isinstance(current, str):
            parsed = _parse_json_text(current)
            if parsed is None:
                break
            current = parsed
            continue

        if not isinstance(current, dict):
            break

        if _looks_like_score_result(current):
            return current

        moved = False
        for key in WRAPPER_KEYS:
            nested = current.get(key)
            if isinstance(nested, (dict, str)):
                current = nested
                moved = True
                break
        if moved:
            continue

        break

    if _looks_like_score_result(current):
        return current
    if isinstance(current, dict):
        return current
    return {}
