#!/usr/bin/env python3
"""从评审表拉取分数，结合主表名字，给 tarce分析 文件夹里的无分数文件重命名。"""

import json
import os
import re
import requests

APP_ID = "cli_a95d98f987785bdb"
APP_SECRET = "m5ukOIrL7t1ULg5TcVbRkDCCyULEmuHX"

# 主表
MAIN_APP_TOKEN = "TdiNb881Ja89b9s6FwNcOH1Vn4c"
MAIN_TABLE_ID = "tblE2Qdot3No2kUC"

# 评审表
REVIEW_APP_TOKEN = "INiPbaSwsaKCffszIDwc1dYPnph"
REVIEW_TABLE_ID = "tblzyPQ33dOle6lY"

OUTPUT_DIR = "/Users/xiaoxu/Desktop/智识/tarce分析"


def get_token():
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=30,
    )
    return resp.json()["tenant_access_token"]


def get_all_records(token, app_token, table_id):
    all_records = []
    page_token = None
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100"
        if page_token:
            url += f"&page_token={page_token}"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            print(f"ERROR: {json.dumps(data, ensure_ascii=False)}")
            break
        items = data.get("data", {}).get("items", [])
        all_records.extend(items)
        if not data["data"].get("has_more"):
            break
        page_token = data["data"].get("page_token")
    return all_records


def extract_person_name(field_value):
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        if isinstance(first, dict):
            return first.get("name", first.get("text", ""))
    if isinstance(field_value, dict):
        return field_value.get("name", field_value.get("text", ""))
    return str(field_value)[:20] if field_value else ""


def extract_score(text):
    """从机审说明/备注中提取分数。"""
    if not text:
        return None
    text = str(text)
    # 尝试解析 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for key in ["total_score", "score", "总分", "final_score", "综合得分"]:
                if key in obj:
                    val = obj[key]
                    if isinstance(val, (int, float)):
                        return int(val)
            # 递归查找嵌套
            for v in obj.values():
                if isinstance(v, dict):
                    for key in ["total_score", "score", "总分"]:
                        if key in v:
                            val = v[key]
                            if isinstance(val, (int, float)):
                                return int(val)
    except (json.JSONDecodeError, TypeError):
        pass
    # 正则
    m = re.search(r"(?:总分|total_score|score|得分|综合得分)[：:\s]*(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*100", text)
    if m:
        return int(m.group(1))
    return None


def main():
    token = get_token()

    # 拉评审表
    print("拉取评审表...")
    review_records = get_all_records(token, REVIEW_APP_TOKEN, REVIEW_TABLE_ID)
    print(f"评审表共 {len(review_records)} 条记录")

    if review_records:
        print(f"评审表字段: {list(review_records[0]['fields'].keys())}")

    # 建立 名字 → 分数 映射
    name_to_score = {}
    print("\n评审表记录:")
    for i, rec in enumerate(review_records):
        f = rec["fields"]
        name = str(f.get("专家姓名", ""))
        note = f.get("机审说明", "")
        remark = f.get("机审备注", "")
        score = extract_score(note)
        if score is None:
            score = extract_score(remark)
        score_str = str(score) if score is not None else "无"
        print(f"  {i+1:2d}. {name:12s} | 分数={score_str}")
        if name and score is not None:
            name_to_score[name] = score

    # 也拉主表，看主表里有没有分数
    print("\n拉取主表...")
    main_records = get_all_records(token, MAIN_APP_TOKEN, MAIN_TABLE_ID)
    print(f"主表共 {len(main_records)} 条记录")

    print("\n主表记录:")
    for i, rec in enumerate(main_records):
        f = rec["fields"]
        name = extract_person_name(f.get("提交人", ""))
        note = f.get("机审说明", "")
        remark = f.get("机审备注", "")
        score = extract_score(note)
        if score is None:
            score = extract_score(remark)
        score_str = str(score) if score is not None else "无"
        print(f"  {i+1:2d}. {name:12s} | 分数={score_str}")
        if name and score is not None and name not in name_to_score:
            name_to_score[name] = score

    print(f"\n分数映射: {name_to_score}")

    # 重命名文件
    print(f"\n--- 重命名无分数文件 ---")
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        if not fname.endswith(".jsonl"):
            continue
        if "无分数" not in fname:
            continue
        # 提取名字
        name = fname.replace("-无分数.jsonl", "")
        if name in name_to_score:
            score = name_to_score[name]
            new_fname = f"{name}-{score}.jsonl"
            old_path = os.path.join(OUTPUT_DIR, fname)
            new_path = os.path.join(OUTPUT_DIR, new_fname)
            os.rename(old_path, new_path)
            print(f"  {fname} → {new_fname}")
        else:
            print(f"  {fname} — 未找到分数，保持不变")

    print("\n最终文件列表:")
    for fname in sorted(os.listdir(OUTPUT_DIR)):
        if fname.endswith(".jsonl"):
            print(f"  {fname}")


if __name__ == "__main__":
    main()
