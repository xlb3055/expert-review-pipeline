#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
core — 可复用工具箱

提供飞书 API、配置加载、流水线执行、Processor 框架等通用能力。
trace_parser / trace_extractor / daytona_runner 按需 import，不在此导出。
新项目只需在 projects/<name>/ 下新建 config.yaml + prompt + schema 即可运行。
"""

from core.config_loader import load_project_config, get_field_name, get_sink_field_name
from core.ctx_utils import load_ctx_data, save_ctx_data
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_attachment_file_token,
    extract_attachment_url,
    extract_link_url,
)
from core.feishu_nodes import (
    load_json_value,
    load_json_object,
    fetch_record_to_data,
    query_records_to_data,
    extract_data_payload,
    extract_batch_items,
    build_update_fields,
    build_batch_update_records,
    write_record_from_data,
    batch_write_records_from_data,
)
from core.pipeline_runner import run_pipeline
