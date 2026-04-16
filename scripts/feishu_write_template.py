#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
火山引擎统一写模板：一个入口兼容单条写入和批量写入。

mode:
- single: 回写单条记录
- batch: 批量回写多条记录
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from core.feishu_nodes import (
    batch_write_records_from_data,
    extract_batch_items,
    extract_data_payload,
    load_json_object,
    load_json_value,
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
    parser = argparse.ArgumentParser(description="飞书统一写模板")
    parser.add_argument("--mode", default="", help="single | batch；默认读 FEISHU_WRITE_MODE")
    parser.add_argument("--record-id", default="", help="single 模式需要的 record_id")
    parser.add_argument("--app-id", default="", help="飞书应用 app_id；默认读 FEISHU_APP_ID / APP_ID")
    parser.add_argument("--app-secret", default="", help="飞书应用 app_secret；默认读 FEISHU_APP_SECRET / APP_SECRET")
    parser.add_argument("--app-token", default="", help="飞书多维表 app_token；默认读 BITABLE_APP_TOKEN")
    parser.add_argument("--table-id", default="", help="飞书多维表 table_id；默认读 BITABLE_TABLE_ID")
    parser.add_argument("--field-mapping-json", default="", help='JSON 字符串，形如 {"review_status":"审核状态"}')
    parser.add_argument("--field-mapping-file", default="", help="输出字段映射 JSON 文件路径")
    parser.add_argument("--status-mapping-json", default="", help="可选状态映射 JSON 字符串")
    parser.add_argument("--status-mapping-file", default="", help="可选状态映射 JSON 文件路径")
    parser.add_argument("--data-json", default="", help="待回写的 JSON 字符串")
    parser.add_argument("--data-file", default="", help="待回写的 JSON 文件路径")
    parser.add_argument("--chunk-size", type=int, default=500, help="batch 模式每批写入多少条")
    parser.add_argument("--output-file", default="", help="输出回写摘要 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只生成回写内容，不真正调用飞书更新")
    args = parser.parse_args()

    mode = _get_arg_or_env(args.mode, "FEISHU_WRITE_MODE").lower() or "single"
    if mode not in {"single", "batch"}:
        print(f"错误: 不支持的 mode: {mode}", file=sys.stderr)
        return 1

    app_id = _get_arg_or_env(args.app_id, "FEISHU_APP_ID", "APP_ID")
    app_secret = _get_arg_or_env(args.app_secret, "FEISHU_APP_SECRET", "APP_SECRET")
    app_token = _get_arg_or_env(args.app_token, "BITABLE_APP_TOKEN")
    table_id = _get_arg_or_env(args.table_id, "BITABLE_TABLE_ID")
    field_mapping_json = _get_arg_or_env(args.field_mapping_json, "DATA_SINK_FIELD_MAPPING_JSON")
    field_mapping_file = _get_arg_or_env(args.field_mapping_file, "DATA_SINK_FIELD_MAPPING_FILE")
    status_mapping_json = _get_arg_or_env(args.status_mapping_json, "STATUS_MAPPING_JSON")
    status_mapping_file = _get_arg_or_env(args.status_mapping_file, "STATUS_MAPPING_FILE")
    data_json = _get_arg_or_env(
        args.data_json,
        "BATCH_WRITE_DATA_JSON",
        "WRITEBACK_DATA_JSON",
    )
    data_file = _get_arg_or_env(
        args.data_file,
        "BATCH_WRITE_DATA_FILE",
        "WRITEBACK_DATA_FILE",
    )

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
    if mode == "single" and not args.record_id:
        missing.append("record_id")
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

    payload = load_json_value(data_json, data_file, label="write_data")
    client = FeishuClient(app_id, app_secret)

    if mode == "single":
        data = extract_data_payload(payload)
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
    else:
        items = extract_batch_items(payload)
        summary = batch_write_records_from_data(
            client,
            app_token,
            table_id,
            items,
            field_mapping,
            status_mapping=status_mapping,
            dry_run=args.dry_run,
            chunk_size=args.chunk_size,
        )

    if args.output_file:
        _write_json(args.output_file, summary)
        print(f"写模板输出已写出: {args.output_file}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
