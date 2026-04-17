#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Trace JSONL 解析器

解析 Claude Code 生成的 .jsonl trace 日志，提取对话轮次、模型名、
工具调用等统计信息，供粗筛和 AI 评审使用。
"""

import json
from dataclasses import dataclass, field


@dataclass
class TraceAnalysis:
    is_valid: bool = False                # 文件是否有效
    conversation_rounds: int = 0          # 对话轮次（human 消息数量）
    model_name: str = ""                  # 使用的模型名
    is_sota_model: bool = False           # 是否为 claude-opus 系列
    has_tool_calls: bool = False          # 是否包含工具调用记录
    tool_call_count: int = 0             # 工具调用次数
    total_lines: int = 0                  # JSONL 总行数
    errors: list = field(default_factory=list)  # 解析错误信息


def _content_has_tool_result(content) -> bool:
    """检查 content 列表中是否包含 tool_result 块。"""
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for b in content
    )


def _normalize_entry(raw: dict) -> dict | None:
    """
    统一三种 JSONL trace 格式为相同的内部表示。

    格式A（旧 / Claude Code CLI streaming）:
        {"type": "human", "content": ..., "model": ...}
    格式B（新 Claude Code session export）:
        {"recordType": "message", "message": {"type": "user", "model": ..., "toolCalls": [...]}}
    格式C（Claude VSCode 插件 streaming）:
        {"type": "assistant", "message": {"role": "assistant", "model": ..., "content": [...]}}
        顶层有 type，但 content/model 在 message 子对象里。

    返回一个扁平 dict，关键字段（content, model, toolCalls 等）均在顶层。
    """
    if not isinstance(raw, dict):
        return None

    # 跳过非消息类型的行
    entry_type = raw.get("type", "")
    if raw.get("recordType") == "session":
        return None
    if entry_type in ("file-history-snapshot",):
        return None

    # 格式B: recordType="message"，message 子对象包含所有字段
    if raw.get("recordType") == "message":
        msg = raw.get("message")
        if not isinstance(msg, dict):
            return None
        normalized = dict(msg)
        if "content" not in normalized and "text" in normalized:
            normalized["content"] = normalized["text"]
        return normalized

    # 格式A / C: 顶层有 type
    # 如果同时有 message 子对象，把 message 中缺失的关键字段合并到顶层
    if entry_type:
        msg = raw.get("message")
        if isinstance(msg, dict):
            # 格式C: content/model/toolCalls 等在 message 中
            merged = dict(raw)
            for key in ("content", "model", "role", "toolCalls", "toolResults"):
                if key not in merged and key in msg:
                    merged[key] = msg[key]
            # 也把 toolUseResult（VSCode 格式的工具返回标记）提升
            # content 里嵌套的 tool_result 块也需要能被识别
            return merged
        return raw

    # 既没有 recordType 也没有 type，尝试从 message 子对象提取
    msg = raw.get("message")
    if isinstance(msg, dict) and "type" in msg:
        normalized = dict(msg)
        if "content" not in normalized and "text" in normalized:
            normalized["content"] = normalized["text"]
        return normalized

    return raw


def parse_trace_file(filepath: str) -> TraceAnalysis:
    """
    逐行读取 JSONL trace 文件，提取统计信息。

    轮次计算: 统计 type 为 "human"/"user" 的消息数量
    模型检测: 从 assistant 消息的 model 字段提取
    SOTA 判定: model 中包含 "opus"（不区分大小写）
    工具调用: 检查 type 为 "tool_use"、含 tool_use content 块、或 toolCalls 字段的记录
    """
    analysis = TraceAnalysis()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        analysis.errors.append(f"文件不存在: {filepath}")
        return analysis
    except Exception as e:
        analysis.errors.append(f"文件读取失败: {e}")
        return analysis

    if not lines:
        analysis.errors.append("文件为空")
        return analysis

    analysis.is_valid = True
    analysis.total_lines = len(lines)
    models_seen = set()

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            analysis.errors.append(f"第 {i + 1} 行 JSON 解析失败: {e}")
            continue

        entry = _normalize_entry(raw)
        if entry is None:
            continue

        entry_type = entry.get("type", "")

        # 统计用户消息轮次（兼容 "human" 和 "user" 两种格式）
        if entry_type in ("human", "user"):
            # 排除 isMeta 标记的系统消息
            # 排除工具返回消息（不同格式字段名不同）:
            #   - 格式B (session export): toolResults
            #   - 格式C (VSCode): toolUseResult 或 content 中含 tool_result 块
            is_tool_return = (
                entry.get("toolResults")
                or entry.get("toolUseResult")
                or _content_has_tool_result(entry.get("content"))
            )
            if not entry.get("isMeta", False) and not is_tool_return:
                analysis.conversation_rounds += 1

        # 提取模型名（从 assistant 消息）
        if entry_type == "assistant":
            model = entry.get("model", "")
            if model:
                models_seen.add(model)
        # 兼容：某些 trace 格式在其他类型的记录中也有 model 字段
        if entry.get("model"):
            models_seen.add(entry["model"])

        # 检测工具调用 —— 旧格式: type="tool_use" 顶层条目
        if entry_type == "tool_use":
            analysis.has_tool_calls = True
            analysis.tool_call_count += 1

        # 检查 assistant 消息中内嵌的 tool_use content 块（格式A）
        if entry_type == "assistant":
            content = entry.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        analysis.has_tool_calls = True
                        analysis.tool_call_count += 1

        # 新格式: assistant 消息中的 toolCalls 列表
        if entry_type == "assistant":
            tool_calls = entry.get("toolCalls", [])
            if isinstance(tool_calls, list) and tool_calls:
                analysis.has_tool_calls = True
                analysis.tool_call_count += len(tool_calls)

        # 检查 tool_result / toolResults / toolUseResult（证明确实执行了工具）
        if entry_type == "tool_result":
            analysis.has_tool_calls = True
        if entry.get("toolResults") or entry.get("toolUseResult"):
            analysis.has_tool_calls = True

    # 确定模型名称
    if models_seen:
        # 优先选取 opus 模型，否则取最后一个
        for m in models_seen:
            if "opus" in m.lower():
                analysis.model_name = m
                break
        if not analysis.model_name:
            analysis.model_name = sorted(models_seen)[-1]

    # 判断是否为 SOTA 模型（claude-opus 系列）
    analysis.is_sota_model = "opus" in analysis.model_name.lower() if analysis.model_name else False

    return analysis


def truncate_trace_content(filepath: str, max_rounds: int = 50, max_bytes: int = 512000) -> str:
    """
    读取 trace 文件内容，若过长则截断。

    返回截断后的文本内容，供 AI 评审使用。
    截断策略: 保留前 max_rounds 轮对话内容，或不超过 max_bytes 字节。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"[读取 trace 文件失败: {e}]"

    if not lines:
        return "[trace 文件为空]"

    kept_lines = []
    human_count = 0
    total_bytes = 0

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        try:
            raw = json.loads(line_stripped)
        except json.JSONDecodeError:
            continue

        entry = _normalize_entry(raw) if isinstance(raw, dict) else raw
        if isinstance(entry, dict) and entry.get("type") in ("human", "user"):
            is_tool_return = (
                entry.get("toolResults")
                or entry.get("toolUseResult")
                or _content_has_tool_result(entry.get("content"))
            )
            if not entry.get("isMeta", False) and not is_tool_return:
                human_count += 1

        if human_count > max_rounds:
            kept_lines.append(f'[... 已截断，共 {len(lines)} 行，仅保留前 {max_rounds} 轮对话 ...]')
            break

        line_bytes = len(line_stripped.encode("utf-8"))
        if total_bytes + line_bytes > max_bytes:
            kept_lines.append(f'[... 已截断，原始文件 {len(lines)} 行，因超过 {max_bytes} 字节限制 ...]')
            break

        kept_lines.append(line_stripped)
        total_bytes += line_bytes

    return "\n".join(kept_lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python3 -m core.trace_parser <trace.jsonl>")
        sys.exit(1)

    result = parse_trace_file(sys.argv[1])
    print(f"有效: {result.is_valid}")
    print(f"对话轮次: {result.conversation_rounds}")
    print(f"模型: {result.model_name}")
    print(f"是否 SOTA: {result.is_sota_model}")
    print(f"有工具调用: {result.has_tool_calls}")
    print(f"工具调用次数: {result.tool_call_count}")
    print(f"总行数: {result.total_lines}")
    if result.errors:
        print(f"错误: {result.errors}")
