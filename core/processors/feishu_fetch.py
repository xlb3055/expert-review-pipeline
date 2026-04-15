#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
内置 Processor: feishu_fetch

按 data_source.fields 配置从飞书多维表格拉取记录，
将每个字段 normalize 后存入 ctx.data[逻辑名]。
同时保存原始值 ctx.data["_raw_字段名"] 供附件等场景使用。
"""

import json
import os
import sys

from core.feishu_utils import normalize_field_value
from core.processors import BaseProcessor, ProcessorContext, register


@register("feishu_fetch")
class FeishuFetchProcessor(BaseProcessor):

    def run(self, ctx: ProcessorContext) -> int:
        config = ctx.config
        client = ctx.client

        print("===== feishu_fetch: 拉取飞书记录 =====")
        print(f"Record ID: {ctx.record_id}")

        # 获取字段映射: 优先 data_source.fields, 回退 field_mapping
        ds = config.get("data_source", {})
        fields_map = ds.get("fields", {})
        if not fields_map:
            fields_map = config.get("field_mapping", {})

        if not fields_map:
            print("警告: 未配置 data_source.fields 或 field_mapping，跳过拉取", file=sys.stderr)
            return 0

        # 从飞书获取记录
        record = client.get_record(ctx.app_token, ctx.table_id, ctx.record_id)
        fields = record.get("fields", {})
        print(f"获取到 {len(fields)} 个字段")

        # 将每个逻辑字段存入 ctx.data
        for logical_name, feishu_field_name in fields_map.items():
            raw_value = fields.get(feishu_field_name)
            ctx.data[logical_name] = normalize_field_value(raw_value)
            ctx.data[f"_raw_{logical_name}"] = raw_value
            print(f"  {logical_name} ({feishu_field_name}): {str(ctx.data[logical_name])[:80]}")

        # 保留完整的原始 fields 供后续 processor 使用
        ctx.data["_raw_fields"] = fields

        # 写出 ctx_data.json 供后续 script 阶段使用
        if ctx.workspace_dir:
            ctx_data_path = os.path.join(ctx.workspace_dir, "ctx_data.json")
            with open(ctx_data_path, "w", encoding="utf-8") as f:
                json.dump(ctx.data, f, ensure_ascii=False, indent=2)
            print(f"ctx_data.json 已写出: {ctx_data_path}")

        print(f"feishu_fetch 完成: ctx.data 包含 {len(fields_map)} 个逻辑字段")
        return 0
