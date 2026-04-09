#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
第三层：结果回填飞书多维表格

读取粗筛和 AI 评审的结果 JSON，提取分数，判定最终结论，
回填飞书多维表格对应字段。

用法:
  python3 writeback.py --record-id <record_id> \
    --pre-screen-result /workspace/pre_screen_result.json \
    --ai-review-result /workspace/ai_review_result.json
"""

import argparse
import json
import os
import sys

from feishu_utils import (
    check_required_env,
    get_feishu_token,
    update_record,
)


# ---------- 分数阈值 ----------

THRESHOLD_PASS = 7       # 总分 >= 7 → 通过
THRESHOLD_REVIEW = 5     # 总分 5-6 → 待人工复核
                          # 总分 < 5 → 拒绝


def read_json_file(path: str) -> dict:
    """读取 JSON 文件。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_scores(ai_result: dict) -> dict:
    """
    从 AI 评审结果中提取 3 个维度分数和总分。

    返回:
      {
        "task_complexity": int,
        "iteration_quality": int,
        "professional_judgment": int,
        "total_score": int,
      }
    """
    def get_score(key: str, max_score: int) -> int:
        val = ai_result.get(key, {})
        if isinstance(val, dict):
            score = val.get("score", 0)
        elif isinstance(val, (int, float)):
            score = val
        else:
            score = 0
        return max(0, min(int(score), max_score))

    tc = get_score("task_complexity", 3)
    iq = get_score("iteration_quality", 3)
    pj = get_score("professional_judgment", 4)
    total = tc + iq + pj

    # 也检查 AI 是否直接给出了 total_score
    ai_total = ai_result.get("total_score")
    if isinstance(ai_total, (int, float)) and int(ai_total) == total:
        pass  # 一致，没问题
    elif isinstance(ai_total, (int, float)):
        print(f"注意: AI 给出的总分 {ai_total} 与计算值 {total} 不一致，使用计算值")

    return {
        "task_complexity": tc,
        "iteration_quality": iq,
        "professional_judgment": pj,
        "total_score": total,
    }


def determine_conclusion(total_score: int, pre_screen_status: str) -> str:
    """
    根据总分和粗筛状态判定最终结论。

    - 粗筛拒绝 → 拒绝
    - 总分 >= 7 → 通过
    - 总分 5-6 → 待人工复核
    - 总分 < 5 → 拒绝
    """
    if pre_screen_status == "拒绝":
        return "拒绝"

    if total_score >= THRESHOLD_PASS:
        return "通过"
    elif total_score >= THRESHOLD_REVIEW:
        return "待人工复核"
    else:
        return "拒绝"


def run_writeback(record_id: str, pre_screen_path: str, ai_review_path: str) -> int:
    """
    执行结果回填。

    返回: 0=成功, 1=失败
    """
    check_required_env()

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

    # 3. 提取分数
    scores = extract_scores(ai_result)
    print(f"\n分数:")
    print(f"  任务复杂度: {scores['task_complexity']}/3")
    print(f"  迭代质量:   {scores['iteration_quality']}/3")
    print(f"  专业判断:   {scores['professional_judgment']}/4")
    print(f"  总分:       {scores['total_score']}/10")

    # 4. 判定最终结论
    conclusion = determine_conclusion(scores["total_score"], pre_screen_status)
    print(f"\n最终结论: {conclusion}")

    # 5. 确定 AI 评审状态
    ai_status = "待审"
    if ai_result.get("error"):
        ai_status = "待人工复核"
    elif scores["total_score"] >= THRESHOLD_PASS:
        ai_status = "通过"
    elif scores["total_score"] >= THRESHOLD_REVIEW:
        ai_status = "待人工复核"
    else:
        ai_status = "拒绝"

    # 6. 回填飞书
    print("\n--- 回填飞书 ---")
    token = get_feishu_token()

    update_fields = {
        "AI评审状态": ai_status,
        "AI评审结果": json.dumps(ai_result, ensure_ascii=False, indent=2),
        "任务复杂度": scores["task_complexity"],
        "迭代质量": scores["iteration_quality"],
        "专业判断": scores["professional_judgment"],
        "最终结论": conclusion,
    }

    try:
        update_record(token, record_id, update_fields)
        print("飞书回填成功")
        for k, v in update_fields.items():
            if k == "AI评审结果":
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
    parser.add_argument(
        "--pre-screen-result",
        default="/workspace/pre_screen_result.json",
        help="粗筛结果 JSON 路径",
    )
    parser.add_argument(
        "--ai-review-result",
        default="/workspace/ai_review_result.json",
        help="AI 评审结果 JSON 路径",
    )
    args = parser.parse_args()

    try:
        exit_code = run_writeback(args.record_id, args.pre_screen_result, args.ai_review_result)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
