#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第三层：结果回填飞书多维表格

读取粗筛和 AI 评审的结果 JSON，提取双模块分数（专家能力分 + Trace 资产分），
判定最终结论，回填飞书多维表格对应字段。

用法:
  python3 writeback.py --record-id <record_id> --project-dir <dir>
"""

import argparse
import json
import os
import sys

from core.config_loader import load_project_config, get_field_name
from core.feishu_utils import FeishuClient


def read_json_file(path: str) -> dict:
    """读取 JSON 文件。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_scores(ai_result: dict, module_key: str, dimensions: list) -> dict:
    """
    从 AI 评审结果中提取指定模块的维度分数和总分。

    module_key: "expert_ability" 或 "trace_asset"
    dimensions: config 中对应模块的 dimensions 列表
    """
    module_data = ai_result.get(module_key, {})
    scores = {}
    total = 0

    for dim in dimensions:
        key = dim["key"]
        max_score = dim["max_score"]
        val = module_data.get(key, {})
        if isinstance(val, dict):
            score = val.get("score", 0)
        elif isinstance(val, (int, float)):
            score = val
        else:
            score = 0
        score = max(0, min(int(score), max_score))
        scores[key] = score
        total += score

    scores["total"] = total

    ai_total = module_data.get("total")
    if isinstance(ai_total, (int, float)) and int(ai_total) != total:
        print(f"注意: {module_key} AI 给出的总分 {ai_total} 与计算值 {total} 不一致，使用计算值")

    return scores


def determine_conclusion(expert_total: int, trace_total: int,
                         pre_screen_status: str) -> str:
    """
    根据双模块分数和粗筛状态判定最终结论。

    - 粗筛拒绝 → 拒绝
    - 专家能力 >= 7 → 可储备专家
    - Trace 资产 >= 9 → 高价值trace
    - 两者同时满足 → 可储备专家 + 高价值trace
    - 专家能力 >= 5 或 Trace 资产 >= 6 → 待人工复核
    - 否则 → 拒绝
    """
    if pre_screen_status == "拒绝":
        return "拒绝"

    labels = []
    if expert_total >= 7:
        labels.append("可储备专家")
    if trace_total >= 9:
        labels.append("高价值trace")

    if labels:
        return " + ".join(labels)
    elif expert_total >= 5 or trace_total >= 6:
        return "待人工复核"
    else:
        return "拒绝"


def run_writeback(record_id: str, project_dir: str) -> int:
    """
    执行结果回填。

    返回: 0=成功, 1=失败
    """
    config = load_project_config(project_dir)
    client = FeishuClient.from_config(config)
    fm = config.get("field_mapping", {})
    scoring = config.get("scoring", {})
    workspace = config.get("workspace", {})

    expert_cfg = scoring.get("expert_ability", {})
    trace_cfg = scoring.get("trace_asset", {})
    expert_dims = expert_cfg.get("dimensions", [])
    trace_dims = trace_cfg.get("dimensions", [])

    pre_screen_path = os.environ.get(
        "PRE_SCREEN_RESULT_PATH",
        workspace.get("pre_screen_result_path", "/workspace/pre_screen_result.json"),
    )
    ai_review_path = os.environ.get(
        "AI_REVIEW_RESULT_PATH",
        workspace.get("ai_review_result_path", "/workspace/ai_review_result.json"),
    )

    print("===== 结果回填开始 =====")
    print(f"Record ID: {record_id}")

    # 1. 读取粗筛结果
    print("\n--- 读取粗筛结果 ---")
    try:
        pre_screen = read_json_file(pre_screen_path)
        pre_screen_status = pre_screen.get("粗筛状态", "待审")
        print(f"粗筛状态: {pre_screen_status}")
    except Exception as e:
        print(f"读取粗筛结果失败: {e}", file=sys.stderr)
        pre_screen = {}
        pre_screen_status = "待审"

    # 2. 读取 AI 评审结果
    print("\n--- 读取 AI 评审结果 ---")
    try:
        ai_result = read_json_file(ai_review_path)
        print(f"AI 评审结果键: {list(ai_result.keys())}")
    except Exception as e:
        print(f"读取 AI 评审结果失败: {e}", file=sys.stderr)
        ai_result = {}

    # 3. 提取双模块分数
    expert_scores = extract_scores(ai_result, "expert_ability", expert_dims)
    trace_scores = extract_scores(ai_result, "trace_asset", trace_dims)

    print(f"\n专家能力分:")
    for dim in expert_dims:
        key = dim["key"]
        print(f"  {key}: {expert_scores[key]}/{dim['max_score']}")
    print(f"  总分: {expert_scores['total']}/10")

    print(f"\nTrace 资产分:")
    for dim in trace_dims:
        key = dim["key"]
        print(f"  {key}: {trace_scores[key]}/{dim['max_score']}")
    print(f"  总分: {trace_scores['total']}/12")

    # 4. 判定最终结论
    conclusion = determine_conclusion(
        expert_scores["total"], trace_scores["total"], pre_screen_status,
    )
    print(f"\n最终结论: {conclusion}")

    # 5. 确定 AI 评审状态
    if ai_result.get("error"):
        ai_status = "待人工复核"
    elif conclusion in ("拒绝",):
        ai_status = "拒绝"
    elif "待人工复核" in conclusion:
        ai_status = "待人工复核"
    else:
        ai_status = "通过"

    # 6. 回填飞书
    print("\n--- 回填飞书 ---")

    # 专家能力分字段映射
    expert_dim_mapping = {
        "task_complexity": "task_complexity_score",
        "iteration_quality": "iteration_quality_score",
        "professional_judgment": "professional_judgment_score",
    }

    # Trace 资产分字段映射
    trace_dim_mapping = {
        "authenticity": "authenticity_score",
        "info_density": "info_density_score",
        "tool_loop": "tool_loop_score",
        "correction_value": "correction_value_score",
        "verification_loop": "verification_loop_score",
        "compliance": "compliance_score",
    }

    update_fields = {
        fm.get("ai_review_status", "AI评审状态"): ai_status,
        fm.get("ai_review_result", "AI评审结果"): json.dumps(ai_result, ensure_ascii=False, indent=2),
        fm.get("final_conclusion", "最终结论"): conclusion,
        fm.get("expert_ability_total", "总分"): expert_scores["total"],
        fm.get("trace_asset_total", "Trace资产总分"): trace_scores["total"],
    }

    # 专家能力三子维度
    for dim in expert_dims:
        key = dim["key"]
        logical_name = expert_dim_mapping.get(key, f"{key}_score")
        feishu_field = fm.get(logical_name, key)
        update_fields[feishu_field] = expert_scores[key]

    # Trace 资产六子维度
    for dim in trace_dims:
        key = dim["key"]
        logical_name = trace_dim_mapping.get(key, f"{key}_score")
        feishu_field = fm.get(logical_name, key)
        update_fields[feishu_field] = trace_scores[key]

    try:
        client.update_record(record_id, update_fields)
        print("飞书回填成功")
        for k, v in update_fields.items():
            if k == fm.get("ai_review_result", "AI评审结果"):
                print(f"  {k}: (JSON, {len(str(v))} 字符)")
            else:
                print(f"  {k}: {v}")
    except Exception as e:
        print(f"飞书回填失败: {e}", file=sys.stderr)
        return 1

    print(f"\n===== 结果回填完成 =====")
    return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物结果回填")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    args = parser.parse_args()

    try:
        exit_code = run_writeback(args.record_id, args.project_dir)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
