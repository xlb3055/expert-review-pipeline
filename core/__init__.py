#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
core — 可复用基础层

提供飞书 API、Daytona 沙箱、trace 解析、配置加载、流水线执行等通用能力。
新项目只需在 projects/<name>/ 下新建 config.yaml + 业务脚本即可运行。
"""

from core.config_loader import load_project_config, get_main_field_name
from core.feishu_utils import (
    FeishuClient,
    normalize_field_value,
    extract_attachment_file_token,
    extract_attachment_url,
    extract_link_url,
)
from core.trace_parser import TraceAnalysis, parse_trace_file, truncate_trace_content
from core.trace_extractor import extract_user_focused_content
from core.daytona_runner import DaytonaRunConfig, ClaudeRunResult, run_claude_in_sandbox
