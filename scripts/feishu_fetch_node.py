#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
火山引擎通用取数节点：按运行时参数从任意飞书多维表读取单条记录字段。

示例:
python3 scripts/feishu_fetch_node.py \
  --record-id recxxxx \
  --app-token app_token \
  --table-id table_id \
  --fields-json '{"title":"标题","content":"正文"}' \
  --output-file /workspace/input.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core.feishu_nodes import fetch_record_to_data, load_json_object
from core.feishu_utils import FeishuClient


def _get_arg_or_env(value: str, *env_names: str) -> str:
    if value:
        return value
    for env_name in env_names:
        env_value = os.environ.get(env_name, "")
        if env_value:
            return env_value
    return ""


def _write_json(path: str, payload: dict):
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="飞书通用取数节点")
    parser.add_argument("--record-id", required=True, help="目标记录的 record_id")
    parser.add_argument("--app-id", default="", help="飞书应用 app_id；默认读 FEISHU_APP_ID / APP_ID")
    parser.add_argument("--app-secret", default="", help="飞书应用 app_secret；默认读 FEISHU_APP_SECRET / APP_SECRET")
    parser.add_argument("--app-token", default="", help="飞书多维表 app_token；默认读 BITABLE_APP_TOKEN")
    parser.add_argument("--table-id", default="", help="飞书多维表 table_id；默认读 BITABLE_TABLE_ID")
    parser.add_argument("--fields-json", default="", help='JSON 字符串，形如 {"title":"标题"}')
    parser.add_argument("--fields-file", default="", help="字段映射 JSON 文件路径")
    parser.add_argument("--output-file", default="", help="输出 JSON 文件路径；不传则打印到 stdout")
    args = parser.parse_args()

    app_id = _get_arg_or_env(args.app_id, "FEISHU_APP_ID", "APP_ID")
    app_secret = _get_arg_or_env(args.app_secret, "FEISHU_APP_SECRET", "APP_SECRET")
    app_token = _get_arg_or_env(args.app_token, "BITABLE_APP_TOKEN")
    table_id = _get_arg_or_env(args.table_id, "BITABLE_TABLE_ID")
    fields_json = _get_arg_or_env(args.fields_json, "DATA_SOURCE_FIELDS_JSON")
    fields_file = _get_arg_or_env(args.fields_file, "DATA_SOURCE_FIELDS_FILE")

    missing = []
    if not app_id:
        missing.append("app_id")
    if not app_secret:
        missing.append("app_secret")
    if not app_token:
        missing.append("app_token")
    if not table_id:
        missing.append("table_id")
    if not fields_json and not fields_file:
        missing.append("fields_json/fields_file")
    if missing:
        print(f"错误: 缺少参数: {', '.join(missing)}", file=sys.stderr)
        return 1

    fields_map = load_json_object(fields_json, fields_file, label="data_source.fields")
    client = FeishuClient(app_id, app_secret)
    payload = fetch_record_to_data(
        client,
        app_token,
        table_id,
        args.record_id,
        fields_map,
    )

    if args.output_file:
        _write_json(args.output_file, payload)
        print(f"已写出 {len(fields_map)} 个字段到: {args.output_file}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
