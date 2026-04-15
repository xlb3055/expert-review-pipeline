#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第三层：算分 + 拼结论 + 生成机审说明

从 ctx_data.json 读取 ai_review_result，提取双模块分数，
判定最终结论，把结果写回 ctx_data.json。
实际飞书回填由 feishu_writeback processor 统一执行。

用法:
  python3 writeback.py --record-id <id> --project-dir <dir> --ctx-data-file <path>
"""

import argparse
import json
import os
import sys

from core.ctx_utils import load_ctx_data, save_ctx_data
from core.config_loader import load_project_config


def extract_scores(ai_result: dict, module_key: str, dimensions: list) -> dict:
    """从 AI 评审结果中提取指定模块的维度分数和总分。"""
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


def compute_composite_score(expert_total: int, trace_total: int,
                            expert_max: int = 10, trace_max: int = 12) -> float:
    """综合分 = 能力分百分比 * 50% + 资产分百分比 * 50%，返回 0-100。"""
    expert_pct = (expert_total / expert_max * 100) if expert_max else 0
    trace_pct = (trace_total / trace_max * 100) if trace_max else 0
    return round(expert_pct * 0.5 + trace_pct * 0.5, 1)


def determine_conclusion(expert_total: int, trace_total: int,
                         pre_screen_status: str,
                         pass_score: float = 70,
                         expert_max: int = 10, trace_max: int = 12) -> tuple:
    """根据综合分判定通过/不通过。返回: (conclusion_str, composite_score)"""
    if pre_screen_status == "拒绝":
        return "不通过", 0.0

    score = compute_composite_score(expert_total, trace_total, expert_max, trace_max)

    if score >= pass_score:
        return "通过", score
    else:
        return "不通过", score


EXPERT_DIM_LABELS = {
    "task_complexity": ("任务复杂度", 3),
    "iteration_quality": ("迭代质量", 3),
    "professional_judgment": ("专业判断", 4),
}
TRACE_DIM_LABELS = {
    "authenticity": ("真实性", 2),
    "info_density": ("信息密度", 2),
    "tool_loop": ("工具闭环", 2),
    "correction_value": ("纠偏价值", 2),
    "verification_loop": ("验证闭环", 2),
    "compliance": ("合规可用性", 2),
}


def _build_machine_note(expert_scores: dict, trace_scores: dict,
                        ai_result: dict) -> str:
    """机审说明：纯逐项解析，不含结论。"""
    expert_data = ai_result.get("expert_ability", {})
    trace_data = ai_result.get("trace_asset", {})

    lines = [
        f"专家能力分: {expert_scores['total']}/10 "
        f"(复杂度{expert_scores.get('task_complexity', 0)}/3, "
        f"迭代{expert_scores.get('iteration_quality', 0)}/3, "
        f"判断{expert_scores.get('professional_judgment', 0)}/4)",
    ]
    lines.append("")
    for key, (label, max_s) in EXPERT_DIM_LABELS.items():
        dim = expert_data.get(key, {})
        score = dim.get("score", expert_scores.get(key, 0)) if isinstance(dim, dict) else expert_scores.get(key, 0)
        evidence = dim.get("evidence", "") if isinstance(dim, dict) else ""
        suggestion = dim.get("suggestion", "") if isinstance(dim, dict) else ""
        lines.append(f"▸ {label}: {score}/{max_s}")
        if evidence:
            lines.append(f"  理由: {evidence}")
        if suggestion:
            lines.append(f"  建议: {suggestion}")

    lines.append("")
    lines.append(
        f"Trace资产分: {trace_scores['total']}/12 "
        f"(真实{trace_scores.get('authenticity', 0)}/2, "
        f"密度{trace_scores.get('info_density', 0)}/2, "
        f"工具{trace_scores.get('tool_loop', 0)}/2, "
        f"纠偏{trace_scores.get('correction_value', 0)}/2, "
        f"验证{trace_scores.get('verification_loop', 0)}/2, "
        f"合规{trace_scores.get('compliance', 0)}/2)"
    )
    lines.append("")
    for key, (label, max_s) in TRACE_DIM_LABELS.items():
        dim = trace_data.get(key, {})
        score = dim.get("score", trace_scores.get(key, 0)) if isinstance(dim, dict) else trace_scores.get(key, 0)
        evidence = dim.get("evidence", "") if isinstance(dim, dict) else ""
        suggestion = dim.get("suggestion", "") if isinstance(dim, dict) else ""
        lines.append(f"▸ {label}: {score}/{max_s}")
        if evidence:
            lines.append(f"  理由: {evidence}")
        if suggestion:
            lines.append(f"  建议: {suggestion}")

    return "\n".join(lines)


def _build_machine_remark(conclusion: str, composite_score: float,
                          expert_scores: dict, trace_scores: dict,
                          ai_result: dict, pass_score: float = 70) -> str:
    """机审备注：结论 + 简短人话反馈，发给专家看的。"""
    expert_data = ai_result.get("expert_ability", {})
    trace_data = ai_result.get("trace_asset", {})
    overall = ai_result.get("overall_assessment", "")

    lines = [f"结论: {conclusion}（综合评分 {composite_score:.0f}）"]

    if conclusion == "通过":
        if overall:
            lines.append(overall)
        else:
            lines.append("整体表现良好，符合要求。")
    else:
        if overall:
            lines.append(overall)
        else:
            lines.append(f"综合评分未达及格线（{pass_score:.0f}），请参考以下方向改进。")
        weak = []
        all_dims = list(EXPERT_DIM_LABELS.items()) + list(TRACE_DIM_LABELS.items())
        for key, (label, max_s) in all_dims:
            data = expert_data if key in EXPERT_DIM_LABELS else trace_data
            dim = data.get(key, {})
            s = dim.get("score", 0) if isinstance(dim, dict) else 0
            if s / max_s < 0.5 if max_s else False:
                weak.append(label)
        if weak:
            lines.append(f"建议重点提升: {'、'.join(weak)}")

    return "\n".join(lines)


def run_writeback(record_id: str, project_dir: str, ctx_data_file: str) -> int:
    """
    从 ctx_data.json 读取 ai_review_result，算分拼结论，写回 ctx_data.json。
    """
    config = load_project_config(project_dir)
    scoring = config.get("scoring", {})

    expert_cfg = scoring.get("expert_ability", {})
    trace_cfg = scoring.get("trace_asset", {})
    expert_dims = expert_cfg.get("dimensions", [])
    trace_dims = trace_cfg.get("dimensions", [])

    # 从 ctx_data.json 读取数据
    ctx_data = load_ctx_data(ctx_data_file)

    print("===== 算分 + 拼结论 开始 =====")
    print(f"Record ID: {record_id}")

    # 读取 AI 评审结果
    ai_result = ctx_data.get("ai_review_result", {})
    if not ai_result:
        print("警告: ai_review_result 为空", file=sys.stderr)

    # 读取粗筛结果
    pre_screen_result = ctx_data.get("pre_screen_result", {})
    pre_screen_status = pre_screen_result.get("粗筛状态", "待审")
    print(f"粗筛状态: {pre_screen_status}")

    # 提取双模块分数
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

    # 判定最终结论
    pass_score = scoring.get("pass_score", 70)
    expert_max = expert_cfg.get("max_total", 10)
    trace_max = trace_cfg.get("max_total", 12)
    conclusion, composite_score = determine_conclusion(
        expert_scores["total"], trace_scores["total"], pre_screen_status,
        pass_score=pass_score, expert_max=expert_max, trace_max=trace_max,
    )
    print(f"\n综合分: {composite_score:.0f}/100 → {conclusion}")

    # 组装机审说明（详细）+ 机审备注（人话）
    machine_note = _build_machine_note(expert_scores, trace_scores, ai_result)
    machine_remark = _build_machine_remark(
        conclusion, composite_score, expert_scores, trace_scores,
        ai_result, pass_score=pass_score,
    )

    # 映射结论到审核状态
    conclusion_map = config.get("conclusion_to_status", {})
    if conclusion == "通过":
        review_status = "pass"
    elif pre_screen_status == "拒绝":
        review_status = "reject"
    else:
        review_status = "manual_review"

    # 写回 ctx_data（如果粗筛没有已设置 machine_review_note 则写入）
    if "machine_review_note" not in ctx_data or pre_screen_status != "拒绝":
        ctx_data["machine_review_note"] = machine_note
    ctx_data["machine_review_remark"] = machine_remark
    ctx_data["review_status"] = review_status

    save_ctx_data(ctx_data_file, ctx_data)

    print(f"\n结果已写回 ctx_data.json")
    print(f"  review_status: {review_status}")
    print(f"  machine_review_note: ({len(machine_note)} 字符)")
    print(f"  machine_review_remark: ({len(machine_remark)} 字符)")
    print(f"\n===== 算分 + 拼结论 完成 =====")

    return 0


def main():
    parser = argparse.ArgumentParser(description="专家考核产物算分拼结论")
    parser.add_argument("--record-id", required=True, help="主表 record_id")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    parser.add_argument("--ctx-data-file", required=True, help="ctx_data.json 路径")
    args = parser.parse_args()

    try:
        exit_code = run_writeback(args.record_id, args.project_dir, args.ctx_data_file)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
