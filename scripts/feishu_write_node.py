#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
火山引擎通用写数节点：按运行时参数将 JSON 数据回写到任意飞书多维表单条记录。

示例:
python3 scripts/feishu_write_node.py \
  --record-id recxxxx \
  --app-token app_token \
  --table-id table_id \
  --field-mapping-json '{"review_status":"审核状态","note":"机审说明"}' \
  --data-file /workspace/result.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core.feishu_nodes import (
    extract_data_payload,
    load_json_object,
    write_record_from_data,
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
    parser = argparse.ArgumentParser(description="飞书通用写数节点")
    parser.add_argument("--record-id", required=True, help="目标记录的 record_id")
    parser.add_argument("--app-id", default="", help="飞书应用 app_id；默认读 FEISHU_APP_ID / APP_ID")
    parser.add_argument("--app-secret", default="", help="飞书应用 app_secret；默认读 FEISHU_APP_SECRET / APP_SECRET")
    parser.add_argument("--app-token", default="", help="飞书多维表 app_token；默认读 BITABLE_APP_TOKEN")
    parser.add_argument("--table-id", default="", help="飞书多维表 table_id；默认读 BITABLE_TABLE_ID")
    parser.add_argument("--field-mapping-json", default="", help='JSON 字符串，形如 {"review_status":"审核状态"}')
    parser.add_argument("--field-mapping-file", default="", help="输出字段映射 JSON 文件路径")
    parser.add_argument("--status-mapping-json", default="", help='可选状态映射，形如 {"pass":"审核通过"}')
    parser.add_argument("--status-mapping-file", default="", help="状态映射 JSON 文件路径")
    parser.add_argument("--data-json", default="", help="待回写的 JSON 字符串")
    parser.add_argument("--data-file", default="", help="待回写的 JSON 文件路径")
    parser.add_argument("--output-file", default="", help="输出回写摘要 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只生成回写内容，不真正调用飞书更新")
    args = parser.parse_args()

    app_id = _get_arg_or_env(args.app_id, "FEISHU_APP_ID", "APP_ID")
    app_secret = _get_arg_or_env(args.app_secret, "FEISHU_APP_SECRET", "APP_SECRET")
    app_token = _get_arg_or_env(args.app_token, "BITABLE_APP_TOKEN")
    table_id = _get_arg_or_env(args.table_id, "BITABLE_TABLE_ID")
    field_mapping_json = _get_arg_or_env(args.field_mapping_json, "DATA_SINK_FIELD_MAPPING_JSON")
    field_mapping_file = _get_arg_or_env(args.field_mapping_file, "DATA_SINK_FIELD_MAPPING_FILE")
    status_mapping_json = _get_arg_or_env(args.status_mapping_json, "STATUS_MAPPING_JSON")
    status_mapping_file = _get_arg_or_env(args.status_mapping_file, "STATUS_MAPPING_FILE")
    data_json = _get_arg_or_env(args.data_json, "WRITEBACK_DATA_JSON")
    data_file = _get_arg_or_env(args.data_file, "WRITEBACK_DATA_FILE")

    missing = []
    if not app_id:
        missing.append("app_id")
    if not app_secret:
        missing.append("app_secret")
    if not app_token:
        missing.append("app_token")
    if not table_id:
        missing.append("table_id")
    if not field_mapping_json and not field_mapping_file:
        missing.append("field_mapping_json/field_mapping_file")
    if not data_json and not data_file:
        missing.append("data_json/data_file")
    if missing:
        print(f"错误: 缺少参数: {', '.join(missing)}", file=sys.stderr)
        return 1

    field_mapping = load_json_object(
        field_mapping_json,
        field_mapping_file,
        label="data_sink.field_mapping",
    )
    status_mapping = {}
    if status_mapping_json or status_mapping_file:
        status_mapping = load_json_object(
            status_mapping_json,
            status_mapping_file,
            label="status_mapping",
        )

    payload = load_json_object(data_json, data_file, label="writeback_data")
    data = extract_data_payload(payload)

    client = FeishuClient(app_id, app_secret)
    summary = write_record_from_data(
        client,
        app_token,
        table_id,
        args.record_id,
        data,
        field_mapping,
        status_mapping=status_mapping,
        dry_run=args.dry_run,
    )

    if args.output_file:
        _write_json(args.output_file, summary)
        print(f"回写摘要已写出: {args.output_file}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
