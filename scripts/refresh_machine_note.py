#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
轻量版重跑 AI 评审 — 直接调 OpenRouter API（不走 Daytona 沙箱），
并发处理，重新生成带 suggestion 的评审结果，只回填飞书"机审说明"字段。

用法:
  export FEISHU_APP_ID="..."
  export FEISHU_APP_SECRET="..."
  export OPENROUTER_API_KEY="..."
  python3 scripts/refresh_machine_note.py [--skip rec1,rec2,...] [--concurrency 5]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from openai import OpenAI
from core.config_loader import load_project_config
from core.feishu_utils import FeishuClient
from core.trace_extractor import extract_user_focused_content
from projects.expert_review.ai_review import _build_input_text
from projects.expert_review.writeback import (
    extract_scores,
    determine_conclusion,
    _build_machine_note,
    _build_machine_remark,
    read_json_file,
)

PROJECT_DIR = os.path.join(REPO_ROOT, "projects", "expert_review")


def call_openrouter(prompt_content: str, json_schema: dict,
                    input_text: str, model: str, api_key: str) -> dict:
    """直接调 OpenRouter API 完成评审，返回解析后的 JSON。"""
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt_content},
            {"role": "user", "content": input_text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": json_schema,
        },
        timeout=300,
    )

    raw = resp.choices[0].message.content
    result = json.loads(raw)

    if "expert_review_result" in result and "expert_ability" not in result:
        result = result["expert_review_result"]

    return result


def process_one(record_id, feishu_client, app_token, table_id,
                prompt_content, json_schema, schema_content,
                model, api_key, config, expert_dims, trace_dims,
                machine_note_field, machine_remark_field, workspace_dir,
                pass_score=70, expert_max=10, trace_max=12):
    """处理单条记录：调 API → 保存 → 回填。带重试。"""
    t0 = time.time()
    record_workspace = os.path.join(workspace_dir, record_id)
    trace_path = os.path.join(record_workspace, "trace.jsonl")
    ai_result_path = os.path.join(record_workspace, "ai_review_result.json")
    pre_screen_path = os.path.join(record_workspace, "pre_screen_result.json")

    max_retries = 2
    last_error = ""
    for attempt in range(max_retries):
        try:
            record = feishu_client.get_record(app_token, table_id, record_id)
            fields = record.get("fields", {})
            trace_content = extract_user_focused_content(trace_path, max_bytes=200000)
            input_text = _build_input_text(fields, trace_content, config)

            ai_result = call_openrouter(prompt_content, json_schema,
                                        input_text, model, api_key)

            with open(ai_result_path, "w", encoding="utf-8") as f:
                json.dump(ai_result, f, ensure_ascii=False, indent=2)

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
            feishu_client.update_record(app_token, table_id, record_id, {
                machine_note_field: machine_note,
                machine_remark_field: machine_remark,
            })

            elapsed = time.time() - t0
            return {
                "record_id": record_id, "ok": True, "elapsed": elapsed,
                "conclusion": conclusion, "composite": composite_score,
                "expert": expert_scores["total"], "trace": trace_scores["total"],
            }
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(5)

    elapsed = time.time() - t0
    return {"record_id": record_id, "ok": False, "elapsed": elapsed, "error": last_error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip", default="", help="逗号分隔的 record_id，跳过这些")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6")
    parser.add_argument("--concurrency", type=int, default=5, help="并发数")
    args = parser.parse_args()

    skip_set = set(s.strip() for s in args.skip.split(",") if s.strip())
    api_key = os.environ.get("OPENROUTER_API_KEY", "")

    config = load_project_config(PROJECT_DIR)
    feishu_client = FeishuClient.from_config(config)
    feishu = config["feishu"]
    app_token = feishu["app_token"]
    table_id = feishu["table_id"]
    scoring = config.get("scoring", {})
    mfm = config.get("field_mapping", {})

    expert_dims = scoring["expert_ability"]["dimensions"]
    trace_dims = scoring["trace_asset"]["dimensions"]
    pass_score = scoring.get("pass_score", 70)
    expert_max = scoring["expert_ability"].get("max_total", 10)
    trace_max = scoring["trace_asset"].get("max_total", 12)
    machine_note_field = mfm.get("machine_review_note", "机审说明")
    machine_remark_field = mfm.get("machine_review_remark", "机审备注")
    workspace_dir = os.path.join(REPO_ROOT, "workspace")

    # 读 prompt + schema
    prompt_content = open(os.path.join(PROJECT_DIR, "prompt.md"), encoding="utf-8").read()
    schema_content = open(os.path.join(PROJECT_DIR, "schema.json"), encoding="utf-8").read()
    schema_obj = json.loads(schema_content)
    json_schema = {
        "name": schema_obj.get("name", "expert_review_result"),
        "strict": schema_obj.get("strict", True),
        "schema": schema_obj.get("schema", schema_obj.get("parameters", schema_obj)),
    }

    # 扫描记录
    records = []
    for entry in sorted(os.listdir(workspace_dir)):
        entry_dir = os.path.join(workspace_dir, entry)
        if not os.path.isdir(entry_dir):
            continue
        if os.path.isfile(os.path.join(entry_dir, "trace.jsonl")):
            if entry in skip_set:
                print(f"跳过 {entry}")
            else:
                records.append(entry)

    print(f"待处理: {len(records)} 条, 并发: {args.concurrency}, 模型: {args.model}\n")

    batch_start = time.time()
    success = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(
                process_one, rid, feishu_client, app_token, table_id,
                prompt_content, json_schema, schema_content,
                args.model, api_key, config,
                expert_dims, trace_dims, machine_note_field, machine_remark_field,
                workspace_dir, pass_score, expert_max, trace_max,
            ): rid
            for rid in records
        }

        for future in as_completed(futures):
            r = future.result()
            if r["ok"]:
                success += 1
                print(f"OK  {r['record_id']} {r['elapsed']:.1f}s | {r['conclusion']} "
                      f"({r['composite']:.0f}分) | "
                      f"能力{r['expert']}/10 资产{r['trace']}/12")
            else:
                failed += 1
                print(f"FAIL {r['record_id']} {r['elapsed']:.1f}s | {r['error']}")

    total = time.time() - batch_start
    print(f"\n完成: 成功 {success}, 失败 {failed}, 总计 {len(records)}, 耗时 {total:.1f}s")


if __name__ == "__main__":
    main()
