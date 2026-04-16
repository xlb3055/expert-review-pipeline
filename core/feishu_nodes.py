#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
飞书通用读写节点工具

把“按字段映射读取一条飞书记录”和“按字段映射回写一条飞书记录”
抽成纯函数，供两类场景复用：

1. core/processors/* 中的通用 Processor
2. 火山引擎里按参数调用的独立 CLI 节点脚本
"""

from __future__ import annotations

import json
from typing import Any

from core.feishu_utils import FeishuClient, normalize_field_value


def parse_json_value(text: str, label: str) -> Any:
    """将 JSON 字符串解析为 Python 值。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{label} 不是合法 JSON: {e}") from e


def parse_json_object(text: str, label: str) -> dict:
    """将 JSON 字符串解析为 dict。"""
    obj = parse_json_value(text, label)
    if not isinstance(obj, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")

    return obj


def load_json_value(json_text: str = "", file_path: str = "", label: str = "JSON") -> Any:
    """从 JSON 文本或文件中加载任意 JSON 值。"""
    if json_text and file_path:
        raise ValueError(f"{label} 不能同时通过文本和文件传入")

    if file_path:
        with open(file_path, "r", encoding="utf-8") as f:
            json_text = f.read()

    if not json_text:
        raise ValueError(f"缺少 {label}")

    return parse_json_value(json_text, label)


def load_json_object(json_text: str = "", file_path: str = "", label: str = "JSON") -> dict:
    """从 JSON 文本或文件中加载 dict。"""
    obj = load_json_value(json_text, file_path, label)
    if not isinstance(obj, dict):
        raise ValueError(f"{label} 必须是 JSON 对象")
    return obj


def build_ctx_data_from_fields(
    fields: dict,
    fields_map: dict,
    *,
    include_raw: bool = True,
    include_meta: bool = True,
    meta: dict | None = None,
) -> dict:
    """
    按逻辑名 → 飞书字段名映射，从一条记录的 fields 构造 ctx_data 风格对象。

    返回格式与现有 ctx.data 保持一致，方便直接接到已有业务脚本。
    """
    if not isinstance(fields_map, dict) or not fields_map:
        raise ValueError("fields_map 不能为空，且必须是 dict")

    data = {}
    for logical_name, feishu_field_name in fields_map.items():
        raw_value = fields.get(feishu_field_name)
        data[logical_name] = normalize_field_value(raw_value)
        if include_raw:
            data[f"_raw_{logical_name}"] = raw_value

    if include_raw:
        data["_raw_fields"] = fields

    if include_meta:
        data["_meta"] = meta or {}

    return data


def fetch_record_to_data(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    record_id: str,
    fields_map: dict,
    *,
    include_raw: bool = True,
    include_meta: bool = True,
) -> dict:
    """从飞书拉取单条记录，并转换成 ctx_data 风格对象。"""
    record = client.get_record(app_token, table_id, record_id)
    fields = record.get("fields", {})
    meta = {
        "record_id": record_id,
        "app_token": app_token,
        "table_id": table_id,
    }
    return build_ctx_data_from_fields(
        fields,
        fields_map,
        include_raw=include_raw,
        include_meta=include_meta,
        meta=meta,
    )


def query_records_to_data(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    fields_map: dict,
    *,
    search_body: dict | None = None,
    page_size: int = 100,
    max_records: int = 0,
    include_raw: bool = True,
    include_meta: bool = True,
) -> dict:
    """查询多条记录，并转换成统一 JSON 结构。"""
    records = client.search_all_records(
        app_token,
        table_id,
        body=search_body,
        page_size=page_size,
        max_records=max_records,
    )

    items = []
    for record in records:
        record_id = record.get("record_id", "")
        fields = record.get("fields", {})
        meta = {
            "record_id": record_id,
            "app_token": app_token,
            "table_id": table_id,
        }
        items.append(
            build_ctx_data_from_fields(
                fields,
                fields_map,
                include_raw=include_raw,
                include_meta=include_meta,
                meta=meta,
            )
        )

    return {
        "items": items,
        "count": len(items),
        "_meta": {
            "app_token": app_token,
            "table_id": table_id,
            "page_size": page_size,
            "max_records": max_records,
        },
    }


def extract_data_payload(payload: dict) -> dict:
    """
    从输入 JSON 中提取可用于回写的业务数据。

    兼容两种形式：
    1. {"data": {...}} 结构
    2. 直接平铺的 ctx_data / result JSON
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 dict")

    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def build_update_fields(data: dict, field_mapping: dict, status_mapping: dict | None = None) -> dict:
    """根据逻辑键 → 飞书字段名映射，生成 update_record 所需的 fields。"""
    if not isinstance(data, dict):
        raise ValueError("data 必须是 dict")
    if not isinstance(field_mapping, dict) or not field_mapping:
        raise ValueError("field_mapping 不能为空，且必须是 dict")

    status_mapping = status_mapping or {}
    update_fields = {}
    for data_key, feishu_field_name in field_mapping.items():
        if data_key not in data:
            continue
        value = data[data_key]
        if status_mapping and isinstance(value, str) and value in status_mapping:
            value = status_mapping[value]
        update_fields[feishu_field_name] = value

    return update_fields


def extract_batch_items(payload: Any) -> list[dict]:
    """
    从批量输入 JSON 中提取记录列表。

    支持:
    1. [{...}, {...}]
    2. {"records": [{...}]}
    3. {"items": [{...}]}
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("records", "items"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
    raise ValueError("批量输入必须是数组，或包含 records/items 数组的对象")


def _extract_item_data(item: dict) -> dict:
    data = item.get("data")
    if isinstance(data, dict):
        return data

    return {
        key: value
        for key, value in item.items()
        if key not in {"record_id", "_meta", "_raw_fields"} and not key.startswith("_raw_")
    }


def build_batch_update_records(
    items: list[dict],
    field_mapping: dict,
    *,
    status_mapping: dict | None = None,
) -> list[dict]:
    """把批量输入转换成飞书 batch_update 所需 records 列表。"""
    if not isinstance(items, list) or not items:
        raise ValueError("批量更新 items 不能为空")

    records = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx + 1} 条批量数据不是对象")

        record_id = item.get("record_id")
        if not record_id and isinstance(item.get("_meta"), dict):
            record_id = item["_meta"].get("record_id")
        if not record_id:
            raise ValueError(f"第 {idx + 1} 条批量数据缺少 record_id")

        data = _extract_item_data(item)
        fields = build_update_fields(data, field_mapping, status_mapping=status_mapping)
        if not fields:
            continue

        records.append(
            {
                "record_id": record_id,
                "fields": fields,
            }
        )

    return records


def chunk_records(records: list[dict], chunk_size: int = 500) -> list[list[dict]]:
    """把 records 按 chunk_size 分块。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    return [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]


def write_record_from_data(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    record_id: str,
    data: dict,
    field_mapping: dict,
    *,
    status_mapping: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """按映射将 data 中的值回写到飞书。"""
    update_fields = build_update_fields(data, field_mapping, status_mapping=status_mapping)

    if update_fields and not dry_run:
        client.update_record(app_token, table_id, record_id, update_fields)

    return {
        "record_id": record_id,
        "app_token": app_token,
        "table_id": table_id,
        "updated_fields": update_fields,
        "updated_count": len(update_fields),
        "dry_run": dry_run,
    }


def batch_write_records_from_data(
    client: FeishuClient,
    app_token: str,
    table_id: str,
    items: list[dict],
    field_mapping: dict,
    *,
    status_mapping: dict | None = None,
    dry_run: bool = False,
    chunk_size: int = 500,
) -> dict:
    """按映射批量回写多条记录。"""
    records = build_batch_update_records(
        items,
        field_mapping,
        status_mapping=status_mapping,
    )
    if not records:
        return {
            "app_token": app_token,
            "table_id": table_id,
            "records": [],
            "record_count": 0,
            "chunk_count": 0,
            "chunk_size": chunk_size,
            "dry_run": dry_run,
        }
    chunks = chunk_records(records, chunk_size=chunk_size)

    if not dry_run:
        for chunk in chunks:
            client.batch_update_records(app_token, table_id, chunk)

    return {
        "app_token": app_token,
        "table_id": table_id,
        "records": records,
        "record_count": len(records),
        "chunk_count": len(chunks),
        "chunk_size": chunk_size,
        "dry_run": dry_run,
    }
