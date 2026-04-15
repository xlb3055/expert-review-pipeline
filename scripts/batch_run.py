#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
批量执行流水线 — 拉取飞书表全部记录，逐条跑 pipeline。

跳过已有最终状态的记录（最终审核通过、已拒绝）。

用法:
  export FEISHU_APP_ID="..."
  export FEISHU_APP_SECRET="..."
  export DAYTONA_API_KEY="..."
  export OPENROUTER_API_KEY="..."
  python3 scripts/batch_run.py
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# 项目根目录
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from core.config_loader import load_project_config
from core.feishu_utils import FeishuClient

PROJECT_DIR = os.path.join(REPO_ROOT, "projects", "expert_review")

# 跳过这些状态的记录
SKIP_STATUSES = {"最终审核通过", "已拒绝"}


def get_all_records(client: FeishuClient, app_token: str, table_id: str) -> list:
    """分页拉取全部记录。"""
    token = client.get_token()
    headers = {"Authorization": f"Bearer {token}"}
    all_records = []
    page_token = None

    while True:
        url = (
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records?page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json().get("data", {})
        all_records.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    return all_records


def run_single_pipeline(record_id: str, workspace_dir: str) -> int:
    """为单条记录执行流水线。"""
    # 每条记录用独立 workspace，避免文件冲突
    record_workspace = os.path.join(workspace_dir, record_id)
    os.makedirs(record_workspace, exist_ok=True)

    env = os.environ.copy()
    env["TRACE_OUTPUT_PATH"] = os.path.join(record_workspace, "trace.jsonl")
    env["PRE_SCREEN_RESULT_PATH"] = os.path.join(record_workspace, "pre_screen_result.json")
    env["AI_REVIEW_RESULT_PATH"] = os.path.join(record_workspace, "ai_review_result.json")
    env["PYTHONPATH"] = REPO_ROOT + ":" + env.get("PYTHONPATH", "")

    import subprocess
    cmd = [
        sys.executable, "-m", "core.pipeline_runner",
        "--project-dir", PROJECT_DIR,
        "--record-id", record_id,
    ]
    result = subprocess.run(cmd, env=env, cwd=REPO_ROOT)
    return result.returncode


def main():
    config = load_project_config(PROJECT_DIR)
    client = FeishuClient.from_config(config)
    feishu = config["feishu"]
    app_token = feishu["app_token"]
    table_id = feishu["table_id"]

    print("=" * 60)
    print("批量执行流水线")
    print("=" * 60)

    # 1. 拉取全部记录
    print("\n--- 拉取全部记录 ---")
    all_records = get_all_records(client, app_token, table_id)
    print(f"总记录数: {len(all_records)}")

    # 2. 过滤需要处理的记录
    todo = []
    for r in all_records:
        record_id = r.get("record_id", "")
        fields = r.get("fields", {})
        status = fields.get("审核状态", "")
        if isinstance(status, list):
            status = status[0] if status else ""

        # 提取提交人姓名
        name_val = fields.get("提交人", "")
        if isinstance(name_val, list) and name_val:
            name = name_val[0].get("name", "") if isinstance(name_val[0], dict) else str(name_val[0])
        elif isinstance(name_val, dict):
            name = name_val.get("name", "")
        else:
            name = str(name_val)

        if status in SKIP_STATUSES:
            print(f"  跳过 {record_id} | {name} | 状态: {status}")
        else:
            todo.append((record_id, name, status))
            print(f"  待处理 {record_id} | {name} | 状态: {status or '(空)'}")

    print(f"\n需要处理: {len(todo)} 条，跳过: {len(all_records) - len(todo)} 条")

    if not todo:
        print("没有需要处理的记录")
        return

    # 3. 创建 workspace
    workspace_dir = os.path.join(REPO_ROOT, "workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=3, help="并发数")
    args = parser.parse_args()

    # 4. 并发执行
    results_summary = {"成功": 0, "粗筛拒绝": 0, "失败": 0}
    batch_start = time.time()

    def _run_one(item):
        record_id, name, status = item
        t0 = time.time()
        try:
            exit_code = run_single_pipeline(record_id, workspace_dir)
        except Exception as e:
            exit_code = 99
        elapsed = time.time() - t0
        return record_id, name, exit_code, elapsed

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(_run_one, item): item for item in todo}
        for future in as_completed(futures):
            record_id, name, exit_code, elapsed = future.result()
            if exit_code == 0:
                results_summary["成功"] += 1
                print(f"OK   {record_id} | {name} ({elapsed:.1f}s)")
            elif exit_code == 1:
                results_summary["粗筛拒绝"] += 1
                print(f"SKIP {record_id} | {name} 粗筛拒绝 ({elapsed:.1f}s)")
            else:
                results_summary["失败"] += 1
                print(f"FAIL {record_id} | {name} exit={exit_code} ({elapsed:.1f}s)")

    # 5. 汇总
    total_elapsed = time.time() - batch_start
    print(f"\n{'=' * 60}")
    print(f"批量执行完成 (并发={args.concurrency})")
    print(f"{'=' * 60}")
    print(f"总耗时: {total_elapsed:.1f}s")
    print(f"成功: {results_summary['成功']}")
    print(f"粗筛拒绝: {results_summary['粗筛拒绝']}")
    print(f"失败: {results_summary['失败']}")
    print(f"总计: {len(todo)}")


if __name__ == "__main__":
    main()
