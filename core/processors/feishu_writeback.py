#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
内置 Processor: feishu_writeback

按 data_sink.field_mapping 配置将 ctx.data 中的结果回填飞书多维表格。
支持 status_mapping 做状态值转换（如 "pass" → "审核通过"）。
"""

import json
import os
import sys

from core.feishu_nodes import build_update_fields
from core.processors import BaseProcessor, ProcessorContext, register


@register("feishu_writeback")
class FeishuWritebackProcessor(BaseProcessor):

    def run(self, ctx: ProcessorContext) -> int:
        config = ctx.config
        client = ctx.client

        print("===== feishu_writeback: 回填飞书记录 =====")
        print(f"Record ID: {ctx.record_id}")

        # 从 ctx_data.json 读取最新数据（业务脚本可能已修改）
        if ctx.workspace_dir:
            ctx_data_path = os.path.join(ctx.workspace_dir, "ctx_data.json")
            if os.path.isfile(ctx_data_path):
                try:
                    with open(ctx_data_path, "r", encoding="utf-8") as f:
                        updated = json.load(f)
                    ctx.data.update(updated)
                    print(f"已从 ctx_data.json 读取 {len(updated)} 个键")
                except Exception as e:
                    print(f"警告: 读取 ctx_data.json 失败: {e}", file=sys.stderr)

        # 获取输出字段映射: 优先 data_sink.field_mapping, 回退 field_mapping
        sink = config.get("data_sink", {})
        field_mapping = sink.get("field_mapping", {})
        if not field_mapping:
            field_mapping = config.get("field_mapping", {})

        status_mapping = sink.get("status_mapping", {})
        if not status_mapping:
            status_mapping = config.get("conclusion_to_status", {})

        if not field_mapping:
            print("警告: 未配置 data_sink.field_mapping 或 field_mapping，跳过回填", file=sys.stderr)
            return 0

        update_fields = build_update_fields(
            ctx.data,
            field_mapping,
            status_mapping=status_mapping,
        )

        if not update_fields:
            print("未找到需要回填的字段（ctx.data 中无匹配 data_sink 的键）")
            return 0

        # 回填飞书
        print(f"回填 {len(update_fields)} 个字段:")
        for feishu_name, val in update_fields.items():
            display = str(val)[:100]
            print(f"  {feishu_name}: {display}")

        try:
            client.update_record(ctx.app_token, ctx.table_id, ctx.record_id, update_fields)
            print("feishu_writeback 完成: 回填成功")
        except Exception as e:
            print(f"feishu_writeback 回填失败: {e}", file=sys.stderr)
            return 1

        return 0
