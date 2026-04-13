#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
用已下载的 12 条 trace 本地验证 pre_screen 7 项硬门槛逻辑。

从每条记录的 info.txt 读取任务描述，从 .jsonl 读取 trace，
模拟运行 7 项检查，与 AI审核总表.csv 的人工标注结果对比。

用法:
  python3 scripts/verify_pre_screen.py
"""

import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.trace_parser import parse_trace_file
from projects.expert_review.pre_screen import (
    check_task_authenticity,
    check_trace_integrity,
    check_tool_loop_exists,
    check_final_product_exists,
    check_verification_exists,
    check_trace_product_consistent,
    check_compliance,
)

TRACE_DIR = "/Users/xiaoxu/Desktop/智识/tarce分析"


def load_info(record_dir: str) -> dict:
    """从 info.txt 读取元信息。"""
    info_path = os.path.join(record_dir, "info.txt")
    info = {}
    if not os.path.exists(info_path):
        return info
    with open(info_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析基础字段
    for line in content.split("\n"):
        if ":" in line and "=====" not in line:
            key, _, val = line.partition(":")
            info[key.strip()] = val.strip()

    # 解析任务描述
    if "===== 任务描述 =====" in content:
        desc = content.split("===== 任务描述 =====")[1].strip()
        # 去掉后续的 ===== 段
        if "=====" in desc:
            desc = desc.split("=====")[0].strip()
        info["任务描述"] = desc

    return info


def find_trace_file(record_dir: str) -> str:
    """找到目录下的 .jsonl 文件。"""
    for fname in os.listdir(record_dir):
        if fname.endswith(".jsonl"):
            return os.path.join(record_dir, fname)
    return ""


def load_expected(csv_path: str) -> dict:
    """从 CSV 加载期望结果。"""
    expected = {}
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row["记录"]
            expected[key] = {
                "硬门槛": row["硬门槛"],
                "失败项": row["失败硬门槛"],
            }
    return expected


def run_checks_for_record(record_dir: str) -> dict:
    """对单条记录执行 7 项检查。"""
    info = load_info(record_dir)
    trace_path = find_trace_file(record_dir)
    task_desc = info.get("任务描述", "")

    # 模拟飞书 fields
    fields = {"任务描述": task_desc}

    # 模拟最终产物存在性 — 从 info 判断
    # 大部分记录如果有 trace 且有描述就认为有产物（简化模拟）
    # 实际上我们没有产物链接信息，这里按照"有 trace 就有产物"的假设
    has_trace_file = bool(trace_path)

    # 解析 trace
    trace = parse_trace_file(trace_path) if trace_path else parse_trace_file("/nonexistent")

    # 读取 trace 原始内容
    trace_content = ""
    if trace_path and os.path.exists(trace_path):
        with open(trace_path, "r", encoding="utf-8") as f:
            trace_content = f.read()

    results = []

    # 检查 1: 任务真实性
    c1 = check_task_authenticity(fields, "任务描述", 50)
    results.append(c1)

    # 检查 2: Trace 完整性
    # 模拟 Trace 附件字段
    trace_fields = {}
    if has_trace_file:
        trace_fields["Trace文件"] = [{"file_token": "mock_token", "name": "trace.jsonl"}]
    c2 = check_trace_integrity(trace_fields, "Trace文件", trace, 3)
    results.append(c2)

    # 检查 3: 工具闭环
    c3 = check_tool_loop_exists(trace)
    results.append(c3)

    # 检查 4: 最终产物 — 模拟：除记录01、11外都有
    product_fields = {}
    name = os.path.basename(record_dir)
    if "记录01" not in name and "记录11" not in name:
        product_fields["最终产物"] = {"link": "https://example.com/product"}
    c4 = check_final_product_exists(product_fields, "最终产物", "最终附件")
    results.append(c4)

    # 检查 5: 验证类工具调用
    c5 = check_verification_exists(trace_content)
    results.append(c5)

    # 检查 6: Trace 与产物一致性
    has_product = "最终产物" in product_fields
    c6 = check_trace_product_consistent(trace, has_product)
    results.append(c6)

    # 检查 7: 合规性
    c7 = check_compliance(trace_content)
    results.append(c7)

    # 汇总
    rejected = [r for r in results if not r["passed"] and r.get("action") == "reject"]
    manual_review = [r for r in results if not r["passed"] and r.get("action") == "manual_review"]
    failed_checks = [r["check"] for r in results if not r["passed"]]

    if rejected:
        status = "拒绝"
    elif manual_review:
        status = "待人工复核"
    else:
        status = "通过"

    return {
        "status": status,
        "failed_checks": failed_checks,
        "details": results,
        "trace_info": {
            "rounds": trace.conversation_rounds,
            "tool_calls": trace.tool_call_count,
            "model": trace.model_name,
            "valid": trace.is_valid,
            "lines": trace.total_lines,
        },
    }


# 检查名称映射到 CSV 中的中文名
CHECK_NAME_MAP = {
    "task_authenticity": "任务真实性",
    "trace_integrity": "Trace完整性",
    "tool_loop_exists": "工具闭环",
    "final_product_exists": "最终产物",
    "verification_exists": "验证动作",
    "trace_product_consistent": "Trace-产物一致",
    "compliance_check": "合规可用性",
}


def main():
    csv_path = os.path.join(TRACE_DIR, "AI审核总表.csv")
    expected = load_expected(csv_path) if os.path.exists(csv_path) else {}

    record_dirs = sorted([
        os.path.join(TRACE_DIR, d)
        for d in os.listdir(TRACE_DIR)
        if d.startswith("记录") and os.path.isdir(os.path.join(TRACE_DIR, d))
    ])

    print("=" * 80)
    print("pre_screen 7 项硬门槛本地验证")
    print("=" * 80)

    total = 0
    match_count = 0

    for record_dir in record_dirs:
        name = os.path.basename(record_dir)
        total += 1

        result = run_checks_for_record(record_dir)
        exp = expected.get(name, {})
        exp_status = exp.get("硬门槛", "?")
        exp_failed = exp.get("失败项", "")

        # 映射我们的状态到 CSV 的标注
        # CSV 中: "通过" / "未通过"
        # 我们: "通过" / "拒绝" / "待人工复核"
        our_status_mapped = "通过" if result["status"] == "通过" else "未通过"
        status_match = our_status_mapped == exp_status

        # 对比失败项
        our_failed_cn = [CHECK_NAME_MAP.get(c, c) for c in result["failed_checks"]]
        exp_failed_list = [x.strip() for x in exp_failed.split(";") if x.strip()]

        if status_match:
            match_count += 1
            icon = "OK"
        else:
            icon = "MISMATCH"

        print(f"\n--- {name} [{icon}] ---")
        print(f"  Trace: valid={result['trace_info']['valid']}, "
              f"rounds={result['trace_info']['rounds']}, "
              f"tools={result['trace_info']['tool_calls']}, "
              f"lines={result['trace_info']['lines']}")
        print(f"  我们的结果: {result['status']}")
        print(f"  期望(CSV):  {exp_status}")
        if result["failed_checks"]:
            print(f"  失败检查项: {', '.join(our_failed_cn)}")
        if exp_failed_list:
            print(f"  期望失败项: {', '.join(exp_failed_list)}")

        # 显示不一致的具体差异
        if not status_match:
            print(f"  *** 状态不一致: 我们={our_status_mapped} vs 期望={exp_status}")

    print(f"\n{'=' * 80}")
    print(f"总计: {total} 条记录, 匹配 {match_count}/{total}")
    if match_count == total:
        print("所有记录的硬门槛判定与人工标注一致!")
    else:
        print(f"有 {total - match_count} 条不一致，需要检查。")
    print("=" * 80)


if __name__ == "__main__":
    main()
