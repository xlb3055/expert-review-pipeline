#!/usr/bin/env python3
"""从飞书主表拉取所有 trace 文件，按 名字-分数.jsonl 格式保存到 tarce分析 文件夹。"""

import json
import os
import re
import sys
import tempfile
import requests

APP_ID = "cli_a95d98f987785bdb"
APP_SECRET = "m5ukOIrL7t1ULg5TcVbRkDCCyULEmuHX"
APP_TOKEN = "TdiNb881Ja89b9s6FwNcOH1Vn4c"
TABLE_ID = "tblE2Qdot3No2kUC"

OUTPUT_DIR = "/Users/xiaoxu/Desktop/智识/tarce分析"


def get_token():
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=30,
    )
    return resp.json()["tenant_access_token"]


def get_all_records(token):
    all_records = []
    page_token = None
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records?page_size=100"
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


def extract_score_from_note(note):
    """从机审说明中提取分数。"""
    if not note:
        return None
    note_str = str(note)
    # 尝试解析 JSON
    try:
        obj = json.loads(note_str)
        if isinstance(obj, dict):
            # 看有没有 total_score, score, 总分 等字段
            for key in ["total_score", "score", "总分", "final_score"]:
                if key in obj:
                    val = obj[key]
                    if isinstance(val, (int, float)):
                        return int(val)
    except (json.JSONDecodeError, TypeError):
        pass
    # 正则匹配 "总分: XX" 或 "score: XX" 或 "XX分"
    m = re.search(r"(?:总分|total_score|score|得分)[：:\s]*(\d+)", note_str)
    if m:
        return int(m.group(1))
    # 匹配 XX/100
    m = re.search(r"(\d+)\s*/\s*100", note_str)
    if m:
        return int(m.group(1))
    return None


def download_attachment(token, file_token, output_path, download_url=None):
    if download_url:
        url = download_url
    else:
        url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120, stream=True)
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return os.path.getsize(output_path)


def main():
    token = get_token()
    print(f"Token OK")

    records = get_all_records(token)
    print(f"共 {len(records)} 条记录\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 看已有文件
    existing_files = set(os.listdir(OUTPUT_DIR))
    print(f"tarce分析 文件夹已有 {len(existing_files)} 个文件: {sorted(existing_files)}\n")

    # 先列出所有记录信息
    print(f"{'序号':>4s}  {'提交人':12s}  {'有Trace':6s}  {'分数':6s}  {'审核状态':14s}  {'已存在':6s}")
    print("-" * 70)

    to_download = []

    for i, rec in enumerate(records):
        f = rec["fields"]
        name = extract_person_name(f.get("提交人", ""))
        trace_files = f.get("Trace 文件", [])
        has_trace = bool(trace_files and isinstance(trace_files, list) and len(trace_files) > 0)
        status = str(f.get("审核状态", ""))
        note = f.get("机审说明", "")
        remark = f.get("机审备注", "")

        # 提取分数 - 先看机审说明，再看机审备注
        score = extract_score_from_note(note)
        if score is None:
            score = extract_score_from_note(remark)

        score_str = str(score) if score is not None else "无"

        # 检查是否已存在
        # 已有文件格式: 名字-分数.jsonl
        already_exists = False
        for ef in existing_files:
            if ef.startswith(name) and ef.endswith(".jsonl"):
                already_exists = True
                break

        exist_str = "是" if already_exists else "否"
        print(f"{i+1:4d}  {name:12s}  {'是' if has_trace else '否':6s}  {score_str:6s}  {status:14s}  {exist_str:6s}")

        if has_trace and not already_exists:
            first_att = trace_files[0]
            if isinstance(first_att, dict) and first_att.get("file_token"):
                to_download.append({
                    "name": name,
                    "score": score,
                    "file_token": first_att["file_token"],
                    "url": first_att.get("url", ""),
                    "file_name": first_att.get("name", "trace.jsonl"),
                })

    print(f"\n需要下载: {len(to_download)} 个\n")

    # 下载
    for item in to_download:
        name = item["name"]
        score = item["score"]
        if score is not None:
            output_name = f"{name}-{score}.jsonl"
        else:
            output_name = f"{name}-无分数.jsonl"

        output_path = os.path.join(OUTPUT_DIR, output_name)
        print(f"下载: {name} → {output_name} ...", end=" ")
        try:
            size = download_attachment(token, item["file_token"], output_path, download_url=item["url"])
            print(f"OK ({size} 字节)")
        except Exception as e:
            print(f"失败: {e}")

    print(f"\n完成!")


if __name__ == "__main__":
    main()
