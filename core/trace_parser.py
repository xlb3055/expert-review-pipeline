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


def parse_trace_file(filepath: str) -> TraceAnalysis:
    """
    逐行读取 JSONL trace 文件，提取统计信息。

    轮次计算: 统计 type 为 "human" 的消息数量
    模型检测: 从 assistant 消息的 model 字段提取
    SOTA 判定: model 中包含 "opus"（不区分大小写）
    工具调用: 检查 type 为 "tool_use" 或含 tool_use content 块的记录
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
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            analysis.errors.append(f"第 {i + 1} 行 JSON 解析失败: {e}")
            continue

        if not isinstance(entry, dict):
            continue

        entry_type = entry.get("type", "")

        # 兼容两种 trace 格式：
        # 格式A（旧）: entry.content / entry.model（顶层）
        # 格式B（新 Claude Code session）: entry.message.content / entry.message.model
        message = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}

        # 统计用户消息轮次（兼容 "human" 和 "user" 两种格式）
        if entry_type in ("human", "user"):
            # 排除 isMeta 标记的系统消息
            if not entry.get("isMeta", False):
                analysis.conversation_rounds += 1

        # 提取模型名（从 assistant 消息）
        if entry_type == "assistant":
            model = entry.get("model", "") or message.get("model", "")
            if model:
                models_seen.add(model)
        # 兼容：某些 trace 格式在其他类型的记录中也有 model 字段
        if "model" in entry and entry["model"]:
            models_seen.add(entry["model"])
        if "model" in message and message["model"]:
            models_seen.add(message["model"])

        # 检测工具调用
        if entry_type == "tool_use":
            analysis.has_tool_calls = True
            analysis.tool_call_count += 1

        # 检查 assistant 消息中内嵌的 tool_use content 块
        # 兼容格式A（entry.content）和格式B（entry.message.content）
        if entry_type == "assistant":
            for content_source in (entry.get("content", []), message.get("content", [])):
                if isinstance(content_source, list):
                    for block in content_source:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            analysis.has_tool_calls = True
                            analysis.tool_call_count += 1

        # 检查 tool_result 类型（证明确实执行了工具）
        if entry_type == "tool_result":
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
            entry = json.loads(line_stripped)
        except json.JSONDecodeError:
            continue

        if isinstance(entry, dict) and entry.get("type") in ("human", "user"):
            if not entry.get("isMeta", False):
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
