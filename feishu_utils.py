#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
飞书多维表格 API 工具函数

提供 tenant_access_token 获取、记录读写、附件下载等功能。
复用 pipeline_feishu_bytehouse.py 的 API 模式。
"""

import json
import os
import sys

import requests

# ---------- 环境变量 ----------

def _env(key: str, *alt: str) -> str:
    v = os.environ.get(key)
    if v:
        return v
    for k in alt:
        v = os.environ.get(k)
        if v:
            return v
    return ""


FEISHU_APP_ID = _env("FEISHU_APP_ID", "APP_ID")
FEISHU_APP_SECRET = _env("FEISHU_APP_SECRET", "APP_SECRET")
BITABLE_APP_TOKEN = _env("BITABLE_APP_TOKEN", "APP_TOKEN")
BITABLE_TABLE_ID = _env("BITABLE_TABLE_ID", "COMMIT_TABLE_ID")


def check_required_env():
    """检查必需的飞书环境变量。"""
    required = [
        ("FEISHU_APP_ID", FEISHU_APP_ID),
        ("FEISHU_APP_SECRET", FEISHU_APP_SECRET),
        ("BITABLE_APP_TOKEN", BITABLE_APP_TOKEN),
        ("BITABLE_TABLE_ID", BITABLE_TABLE_ID),
    ]
    missing = [name for name, val in required if not val]
    if missing:
        print(f"错误: 缺少环境变量: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ---------- 字段值标准化 ----------

def normalize_field_value(value) -> str:
    """将飞书多维表格字段值标准化为字符串。"""
    if value is None:
        return ""

    if isinstance(value, (str, int, float, bool)):
        return str(value)

    if isinstance(value, dict):
        if "text" in value and value["text"] is not None:
            return str(value["text"])
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, (str, int, float, bool)):
                parts.append(str(item))
            elif isinstance(item, dict):
                if "text" in item and item["text"] is not None:
                    parts.append(str(item["text"]))
                elif "name" in item and item["name"] is not None:
                    parts.append(str(item["name"]))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return ", ".join([p for p in parts if p])

    return str(value)


# ---------- 飞书 API ----------

def get_feishu_token() -> str:
    """获取飞书 tenant_access_token。"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")
    return data["tenant_access_token"]


def get_record(token: str, record_id: str) -> dict:
    """获取多维表格单条记录。"""
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}"
        f"/tables/{BITABLE_TABLE_ID}/records/{record_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书记录失败: {data}")
    return data.get("data", {}).get("record", {})


def update_record(token: str, record_id: str, fields: dict) -> dict:
    """更新多维表格指定记录的字段。"""
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}"
        f"/tables/{BITABLE_TABLE_ID}/records/{record_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"fields": fields}
    resp = requests.put(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"更新飞书记录失败: {data}")
    return data


def download_attachment(token: str, file_token: str, output_path: str) -> None:
    """
    下载飞书附件到本地文件。

    飞书附件字段的值格式: [{"file_token": "xxx", "name": "trace.jsonl", ...}]
    调用方需先从字段值中提取 file_token。

    API: GET https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download
    """
    url = f"https://open.feishu.cn/open-apis/drive/v1/medias/{file_token}/download"
    headers = {
        "Authorization": f"Bearer {token}",
    }
    resp = requests.get(url, headers=headers, timeout=120, stream=True)
    resp.raise_for_status()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    print(f"附件已下载: {output_path} ({os.path.getsize(output_path)} 字节)")


def extract_attachment_file_token(field_value) -> str | None:
    """
    从飞书附件字段值中提取第一个 file_token。

    附件字段值通常为 list[dict]，每个 dict 含 file_token 和 name。
    """
    if isinstance(field_value, list) and field_value:
        first = field_value[0]
        if isinstance(first, dict):
            return first.get("file_token")
    return None


def extract_link_url(field_value) -> str:
    """
    从飞书超链接字段值中提取 URL。

    超链接字段值可能是 dict {"link": "...", "text": "..."} 或直接字符串。
    """
    if isinstance(field_value, dict):
        return field_value.get("link", "") or field_value.get("text", "")
    if isinstance(field_value, str):
        return field_value
    return ""
