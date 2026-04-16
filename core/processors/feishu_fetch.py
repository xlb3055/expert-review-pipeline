#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
内置 Processor: feishu_fetch

按 data_source.fields 配置从飞书多维表格拉取记录，
将每个字段 normalize 后存入 ctx.data[逻辑名]。
同时保存原始值 ctx.data["_raw_字段名"] 供附件等场景使用。
"""

import os
import sys

from core.feishu_nodes import fetch_record_to_data
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

        data = fetch_record_to_data(
            client,
            ctx.app_token,
            ctx.table_id,
            ctx.record_id,
            fields_map,
        )
        ctx.data.update(data)

        raw_fields = ctx.data.get("_raw_fields", {})
        print(f"获取到 {len(raw_fields)} 个字段")
        for logical_name, feishu_field_name in fields_map.items():
            print(f"  {logical_name} ({feishu_field_name}): {str(ctx.data.get(logical_name, ''))[:80]}")

        # 写出 ctx_data.json 供后续 script 阶段使用
        if ctx.workspace_dir:
            ctx_data_path = os.path.join(ctx.workspace_dir, "ctx_data.json")
            with open(ctx_data_path, "w", encoding="utf-8") as f:
                import json
                json.dump(ctx.data, f, ensure_ascii=False, indent=2)
            print(f"ctx_data.json 已写出: {ctx_data_path}")

        print(f"feishu_fetch 完成: ctx.data 包含 {len(fields_map)} 个逻辑字段")
        return 0
