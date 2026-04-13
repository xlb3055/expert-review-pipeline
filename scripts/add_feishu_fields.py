#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
在飞书评审工作表中新增 7 个 Trace 资产分字段。

用法:
  # 需要先设置环境变量
  export FEISHU_APP_ID=xxx
  export FEISHU_APP_SECRET=xxx

  python3 scripts/add_feishu_fields.py
  python3 scripts/add_feishu_fields.py --dry-run   # 只预览不执行
"""

import argparse
import json
import sys
import requests

# ---------- 评审工作表信息 ----------

APP_TOKEN = "INiPbaSwsaKCffszIDwc1dYPnph"
TABLE_ID = "tblzyPQ33dOle6lY"

# ---------- 需要新增的 7 个字段 ----------

NEW_FIELDS = [
    {"field_name": "Trace资产总分", "type": 2},   # 数字类型
    {"field_name": "真实性",       "type": 2},
    {"field_name": "信息密度",     "type": 2},
    {"field_name": "工具闭环",     "type": 2},
    {"field_name": "纠偏价值",     "type": 2},
    {"field_name": "验证闭环",     "type": 2},
    {"field_name": "合规可用性",   "type": 2},
]


def get_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token。"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def list_existing_fields(token: str) -> list:
    """获取当前表的所有字段名。"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/fields"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取字段列表失败: {data}")
    items = data.get("data", {}).get("items", [])
    return [f["field_name"] for f in items]


def create_field(token: str, field_def: dict) -> dict:
    """创建一个字段。"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/fields"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=field_def, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建字段失败: {data}")
    return data.get("data", {}).get("field", {})


def main():
    import os

    parser = argparse.ArgumentParser(description="飞书评审工作表新增 Trace 资产分字段")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不实际创建")
    args = parser.parse_args()

    app_id = os.environ.get("FEISHU_APP_ID") or os.environ.get("APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET") or os.environ.get("APP_SECRET", "")

    if not app_id or not app_secret:
        print("错误: 请设置环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET", file=sys.stderr)
        sys.exit(1)

    print(f"评审工作表: app_token={APP_TOKEN}, table_id={TABLE_ID}")
    print(f"待新增字段: {len(NEW_FIELDS)} 个\n")

    if args.dry_run:
        print("[DRY-RUN 模式] 只预览不执行\n")
        for f in NEW_FIELDS:
            print(f"  将创建: {f['field_name']} (type={f['type']}, 数字)")
        print("\n去掉 --dry-run 参数即可实际创建。")
        return

    # 获取 token
    print("--- 获取飞书 token ---")
    token = get_token(app_id, app_secret)
    print("token 获取成功\n")

    # 获取已有字段
    print("--- 检查已有字段 ---")
    existing = list_existing_fields(token)
    print(f"当前表已有 {len(existing)} 个字段: {existing}\n")

    # 逐个创建
    print("--- 开始创建字段 ---")
    created = 0
    skipped = 0
    for field_def in NEW_FIELDS:
        name = field_def["field_name"]
        if name in existing:
            print(f"  [跳过] {name} — 已存在")
            skipped += 1
            continue
        try:
            result = create_field(token, field_def)
            field_id = result.get("field_id", "?")
            print(f"  [创建] {name} — field_id={field_id}")
            created += 1
        except Exception as e:
            print(f"  [失败] {name} — {e}")

    print(f"\n完成: 创建 {created} 个, 跳过 {skipped} 个")


if __name__ == "__main__":
    main()
