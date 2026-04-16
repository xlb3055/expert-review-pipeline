#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
轻量刷新：只基于已有的 ai_review_result.json 重新计算结论+备注并回填飞书。
不调 AI API，不重新打分，只应用新的 pass_score 和备注格式。

用法:
  export FEISHU_APP_ID="..."
  export FEISHU_APP_SECRET="..."
  python3 scripts/refresh_remark_only.py
"""

import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core.config_loader import load_project_config
from core.feishu_utils import FeishuClient
from projects.expert_review.writeback import (
    extract_scores,
    determine_conclusion,
    _build_machine_note,
    _build_machine_remark,
    read_json_file,
)

PROJECT_DIR = os.path.join(REPO_ROOT, "projects", "expert_review")


def main():
    config = load_project_config(PROJECT_DIR)
    client = FeishuClient.from_config(config)
    feishu = config["feishu"]
    app_token = feishu["app_token"]
    table_id = feishu["table_id"]
    scoring = config.get("scoring", {})
    mfm = config.get("field_mapping", {})

    expert_dims = scoring["expert_ability"]["dimensions"]
    trace_dims = scoring["trace_asset"]["dimensions"]
    pass_score = scoring.get("pass_score", 80)
    expert_max = scoring["expert_ability"].get("max_total", 10)
    trace_max = scoring["trace_asset"].get("max_total", 12)
    machine_note_field = mfm.get("machine_review_note", "机审说明")
    machine_remark_field = mfm.get("machine_review_remark", "机审备注")
    workspace_dir = os.path.join(REPO_ROOT, "workspace")

    # 扫描有 ai_review_result.json 的记录
    records = []
    for entry in sorted(os.listdir(workspace_dir)):
        entry_dir = os.path.join(workspace_dir, entry)
        ai_path = os.path.join(entry_dir, "ai_review_result.json")
        if os.path.isdir(entry_dir) and os.path.isfile(ai_path):
            records.append(entry)

    print(f"找到 {len(records)} 条有 AI 结果的记录，开始刷新...\n")

    success = 0
    failed = 0
    for record_id in records:
        t0 = time.time()
        try:
            entry_dir = os.path.join(workspace_dir, record_id)
            ai_path = os.path.join(entry_dir, "ai_review_result.json")
            pre_screen_path = os.path.join(entry_dir, "pre_screen_result.json")

            ai_result = read_json_file(ai_path)
            expert_scores = extract_scores(ai_result, "expert_ability", expert_dims)
            trace_scores = extract_scores(ai_result, "trace_asset", trace_dims)

            pre_screen_status = "待审"
            if os.path.isfile(pre_screen_path):
                try:
                    pre = read_json_file(pre_screen_path)
                    pre_screen_status = pre.get("粗筛状态", "待审")
                except Exception:
                    pass

            conclusion, composite_score = determine_conclusion(
                expert_scores["total"], trace_scores["total"], pre_screen_status,
                pass_score=pass_score, expert_max=expert_max, trace_max=trace_max,
            )
            machine_note = _build_machine_note(expert_scores, trace_scores, ai_result)
            machine_remark = _build_machine_remark(
                conclusion, composite_score, expert_scores, trace_scores,
                ai_result, pass_score=pass_score,
            )

            client.update_record(app_token, table_id, record_id, {
                machine_note_field: machine_note,
                machine_remark_field: machine_remark,
            })

            elapsed = time.time() - t0
            success += 1
            print(f"OK  {record_id} {elapsed:.1f}s | {conclusion} "
                  f"({composite_score:.0f}分) | "
                  f"能力{expert_scores['total']}/10 资产{trace_scores['total']}/12")
        except Exception as e:
            elapsed = time.time() - t0
            failed += 1
            print(f"FAIL {record_id} {elapsed:.1f}s | {e}")

    print(f"\n完成: 成功 {success}, 失败 {failed}, 总计 {len(records)}")


if __name__ == "__main__":
    main()
