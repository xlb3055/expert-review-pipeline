#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Trace 用户内容提取器

从 Claude Code JSONL trace 中提取用于 AI 评审的精简内容：
- 用户（human/user）的完整消息文本
- assistant 消息中的工具调用摘要（仅工具名 + 输入摘要，不含完整返回）
- 过滤掉 isMeta 系统消息、tool_result 详细内容、AI 大段回复

目标：让评审模型聚焦于「专家做了什么」而非被 AI 的大量输出淹没。
"""

import json

from core.trace_parser import _normalize_entry


# Claude Code 本地命令噪音，需过滤
_NOISE_PATTERNS = [
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<task-notification>",
    "<task-id>",
    "This session is being continued from a previous conversation",
    "[Request interrupted by user]",
]


def _is_noise(text: str) -> bool:
    return any(p in text for p in _NOISE_PATTERNS)


def _extract_text_from_content(content) -> str:
    """从 content 字段提取纯文本，忽略 tool_use/tool_result 块。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    parts.append(text.strip())
        return "\n\n".join(parts)
    return ""


def _extract_tool_calls_summary(content) -> list:
    """从 assistant content 中提取工具调用摘要（仅工具名 + 输入概要）。"""
    calls = []
    if not isinstance(content, list):
        return calls
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "unknown")
        inp = block.get("input", {})
        # 只保留输入的关键信息，截断长内容
        summary = _summarize_tool_input(name, inp)
        calls.append({"tool": name, "input_summary": summary})
    return calls


def _extract_tool_calls_from_toolCalls(tool_calls: list) -> list:
    """从新格式的 toolCalls 列表中提取工具调用摘要。"""
    calls = []
    if not isinstance(tool_calls, list):
        return calls
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = tc.get("name", "unknown")
        inp = tc.get("input", {})
        summary = _summarize_tool_input(name, inp)
        calls.append({"tool": name, "input_summary": summary})
    return calls


def _summarize_tool_input(tool_name: str, inp: dict) -> str:
    """根据工具类型生成简短的输入摘要。"""
    if not isinstance(inp, dict):
        return str(inp)[:100]

    # Bash / Execute 类
    if tool_name.lower() in ("bash", "execute", "terminal", "shell"):
        cmd = inp.get("command", inp.get("cmd", ""))
        return cmd[:200] if cmd else str(inp)[:100]

    # Read / Write / Edit 类
    if tool_name.lower() in ("read", "write", "edit"):
        path = inp.get("file_path", inp.get("path", ""))
        return path[:200] if path else str(inp)[:100]

    # Glob / Grep / Search 类
    if tool_name.lower() in ("glob", "grep", "search"):
        pattern = inp.get("pattern", inp.get("query", ""))
        return pattern[:200] if pattern else str(inp)[:100]

    # Agent 类
    if tool_name.lower() == "agent":
        prompt = inp.get("prompt", inp.get("description", ""))
        return prompt[:200] if prompt else str(inp)[:100]

    # WebSearch / WebFetch
    if tool_name.lower() in ("websearch", "webfetch"):
        query = inp.get("query", inp.get("url", ""))
        return query[:200] if query else str(inp)[:100]

    # TodoWrite / TaskCreate
    if tool_name.lower() in ("todowrite", "taskcreate"):
        return str(inp.get("subject", inp.get("todos", "")))[:200]

    # 其他工具：取前 100 字符
    return str(inp)[:100]


def extract_user_focused_content(filepath: str,
                                  max_bytes: int = 200000) -> str:
    """
    从 JSONL trace 中提取以用户行为为核心的精简内容。

    返回格式化文本，包含：
    - [用户] 消息的完整文本
    - [工具调用] assistant 使用的工具名 + 输入摘要
    - [AI摘要] assistant 文本回复的前 150 字（仅保留摘要）

    不包含：
    - tool_result 的详细返回内容
    - AI 的完整长回复
    - isMeta 系统消息
    - 本地命令噪音
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"[读取 trace 文件失败: {e}]"

    if not lines:
        return "[trace 文件为空]"

    output_parts = []
    total_bytes = 0
    round_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if not isinstance(raw, dict):
            continue

        entry = _normalize_entry(raw)
        if entry is None:
            continue

        entry_type = entry.get("type", "")

        # ── 用户消息 ──
        if entry_type in ("human", "user"):
            if entry.get("isMeta", False):
                continue
            # 工具返回消息跳过（不同格式字段名不同）:
            #   - 格式B (session export): toolResults
            #   - 格式C (VSCode): toolUseResult 或 content 中含 tool_result 块
            from core.trace_parser import _content_has_tool_result
            if (entry.get("toolResults")
                    or entry.get("toolUseResult")
                    or _content_has_tool_result(entry.get("content"))):
                continue

            content = entry.get("content")
            text = _extract_text_from_content(content)

            if not text or _is_noise(text):
                continue

            round_num += 1
            part = f"\n{'='*60}\n[第{round_num}轮 - 用户消息]\n{'='*60}\n{text}\n"
            part_bytes = len(part.encode("utf-8"))

            if total_bytes + part_bytes > max_bytes:
                output_parts.append(f"\n[... 因超过 {max_bytes} 字节限制已截断 ...]")
                break

            output_parts.append(part)
            total_bytes += part_bytes

        # ── Assistant 消息 ──
        elif entry_type == "assistant":
            content = entry.get("content")

            # 提取工具调用摘要 —— 旧格式: content 列表中的 tool_use 块
            tool_calls = _extract_tool_calls_summary(content)

            # 新格式: toolCalls 列表
            tool_calls.extend(_extract_tool_calls_from_toolCalls(entry.get("toolCalls")))

            # 提取 AI 文本回复的摘要（仅前 150 字）
            ai_text = _extract_text_from_content(content)

            parts_for_this = []

            if ai_text:
                truncated = ai_text[:150]
                if len(ai_text) > 150:
                    truncated += f"... (共{len(ai_text)}字，已截断)"
                parts_for_this.append(f"  [AI回复摘要] {truncated}")

            if tool_calls:
                for tc in tool_calls:
                    parts_for_this.append(
                        f"  [工具调用] {tc['tool']}: {tc['input_summary']}"
                    )

            if parts_for_this:
                part = "\n".join(parts_for_this) + "\n"
                part_bytes = len(part.encode("utf-8"))
                if total_bytes + part_bytes > max_bytes:
                    output_parts.append(f"\n[... 因超过 {max_bytes} 字节限制已截断 ...]")
                    break
                output_parts.append(part)
                total_bytes += part_bytes

        # ── queue-operation (enqueue = 用户输入) ──
        elif entry_type == "queue-operation":
            op = entry.get("operation", "")
            if op == "enqueue":
                content_text = entry.get("content", "")
                if isinstance(content_text, str) and content_text.strip():
                    text = content_text.strip()
                    if not _is_noise(text):
                        round_num += 1
                        part = f"\n{'='*60}\n[第{round_num}轮 - 用户消息(队列)]\n{'='*60}\n{text}\n"
                        part_bytes = len(part.encode("utf-8"))
                        if total_bytes + part_bytes > max_bytes:
                            output_parts.append(f"\n[... 因超过 {max_bytes} 字节限制已截断 ...]")
                            break
                        output_parts.append(part)
                        total_bytes += part_bytes

        # ── 跳过 tool_use / tool_result 顶层条目 ──
        # （这些已在 assistant 消息的 content 中处理）

    if not output_parts:
        return "[未从 trace 中提取到有效用户内容]"

    header = (
        f"[Trace 精简摘要 — 共 {len(lines)} 行, 提取 {round_num} 轮用户消息]\n"
        f"[仅包含：用户完整消息 + AI工具调用摘要 + AI回复前150字]\n"
    )

    return header + "".join(output_parts)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 -m core.trace_extractor <trace.jsonl>")
        sys.exit(1)

    result = extract_user_focused_content(sys.argv[1])
    print(result)
