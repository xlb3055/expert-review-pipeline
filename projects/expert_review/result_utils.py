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

    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
            return obj
        except json.JSONDecodeError:
            continue

    return None


def _walk_candidates(payload: Any, max_nodes: int = 200):
    """广度优先遍历可能包含评审结果的所有候选节点。"""
    queue = [payload]
    seen_text = set()
    visited = 0

    while queue and visited < max_nodes:
        current = queue.pop(0)
        visited += 1

        if isinstance(current, str):
            text = current.strip()
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            parsed = _parse_json_text(current)
            if parsed is not None:
                queue.append(parsed)
            continue

        if isinstance(current, list):
            queue.extend(current)
            continue

        if not isinstance(current, dict):
            continue

        yield current

        # 优先处理常见包装键，再遍历其它字段
        for key in WRAPPER_KEYS:
            nested = current.get(key)
            if isinstance(nested, (dict, list, str)):
                queue.append(nested)

        for key, value in current.items():
            if key in WRAPPER_KEYS:
                continue
            if isinstance(value, (dict, list, str)):
                queue.append(value)


def normalize_ai_result(payload: Any) -> dict:
    """把 Claude/OpenRouter/执行器包装结果归一化为评审结果对象。"""
    for candidate in _walk_candidates(payload):
        if _looks_like_score_result(candidate):
            return candidate

    return {}
