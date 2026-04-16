#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
火山引擎统一读模板：一个入口兼容单条读取和多条查询。

mode:
- record: 读取单条记录
- query: 查询多条记录
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core.feishu_nodes import (
    fetch_record_to_data,
    load_json_object,
    query_records_to_data,
)
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
    parser = argparse.ArgumentParser(description="飞书统一读模板")
    parser.add_argument("--mode", default="", help="record | query；默认读 FEISHU_READ_MODE")
    parser.add_argument("--record-id", default="", help="单条读取时的 record_id")
    parser.add_argument("--app-id", default="", help="飞书应用 app_id；默认读 FEISHU_APP_ID / APP_ID")
    parser.add_argument("--app-secret", default="", help="飞书应用 app_secret；默认读 FEISHU_APP_SECRET / APP_SECRET")
    parser.add_argument("--app-token", default="", help="飞书多维表 app_token；默认读 BITABLE_APP_TOKEN")
    parser.add_argument("--table-id", default="", help="飞书多维表 table_id；默认读 BITABLE_TABLE_ID")
    parser.add_argument("--fields-json", default="", help='JSON 字符串，形如 {"title":"标题"}')
    parser.add_argument("--fields-file", default="", help="字段映射 JSON 文件路径")
    parser.add_argument("--search-body-json", default="", help="查询条件 JSON 字符串")
    parser.add_argument("--search-body-file", default="", help="查询条件 JSON 文件路径")
    parser.add_argument("--page-size", type=int, default=100, help="query 模式单页条数")
    parser.add_argument("--max-records", type=int, default=0, help="query 模式最多取多少条；0 表示取完")
    parser.add_argument("--output-file", default="", help="输出 JSON 文件路径；不传则打印到 stdout")
    args = parser.parse_args()

    mode = _get_arg_or_env(args.mode, "FEISHU_READ_MODE").lower() or "record"
    if mode not in {"record", "query"}:
        print(f"错误: 不支持的 mode: {mode}", file=sys.stderr)
        return 1

    app_id = _get_arg_or_env(args.app_id, "FEISHU_APP_ID", "APP_ID")
    app_secret = _get_arg_or_env(args.app_secret, "FEISHU_APP_SECRET", "APP_SECRET")
    app_token = _get_arg_or_env(args.app_token, "BITABLE_APP_TOKEN")
    table_id = _get_arg_or_env(args.table_id, "BITABLE_TABLE_ID")
    fields_json = _get_arg_or_env(args.fields_json, "DATA_SOURCE_FIELDS_JSON")
    fields_file = _get_arg_or_env(args.fields_file, "DATA_SOURCE_FIELDS_FILE")
    search_body_json = _get_arg_or_env(args.search_body_json, "SEARCH_BODY_JSON", "QUERY_BODY_JSON")
    search_body_file = _get_arg_or_env(args.search_body_file, "SEARCH_BODY_FILE", "QUERY_BODY_FILE")

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
    if mode == "record" and not args.record_id:
        missing.append("record_id")
    if missing:
        print(f"错误: 缺少参数: {', '.join(missing)}", file=sys.stderr)
        return 1

    fields_map = load_json_object(fields_json, fields_file, label="data_source.fields")
    client = FeishuClient(app_id, app_secret)

    if mode == "record":
        payload = fetch_record_to_data(
            client,
            app_token,
            table_id,
            args.record_id,
            fields_map,
        )
    else:
        search_body = {}
        if search_body_json or search_body_file:
            search_body = load_json_object(search_body_json, search_body_file, label="search_body")
        payload = query_records_to_data(
            client,
            app_token,
            table_id,
            fields_map,
            search_body=search_body,
            page_size=args.page_size,
            max_records=args.max_records,
        )

    if args.output_file:
        _write_json(args.output_file, payload)
        print(f"读模板输出已写出: {args.output_file}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
