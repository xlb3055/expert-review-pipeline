#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
配置加载器

加载项目 config.yaml，合并环境变量，提供字段映射查询。
"""

import os
import sys

import yaml


def load_project_config(project_dir: str) -> dict:
    """
    加载项目目录下的 config.yaml。

    优先级：config.yaml 中的值 > 环境变量（仅当 config 值为空时回退环境变量）。
    """
    config_path = os.path.join(project_dir, "config.yaml")
    if not os.path.isfile(config_path):
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # 合并飞书配置：config 优先，空值回退环境变量
    feishu = config.setdefault("feishu", {})
    feishu["app_id"] = (
        feishu.get("app_id") or os.environ.get("FEISHU_APP_ID") or os.environ.get("APP_ID") or ""
    )
    feishu["app_secret"] = (
        feishu.get("app_secret") or os.environ.get("FEISHU_APP_SECRET") or os.environ.get("APP_SECRET") or ""
    )
    feishu["app_token"] = (
        feishu.get("app_token") or os.environ.get("BITABLE_APP_TOKEN") or os.environ.get("APP_TOKEN") or ""
    )
    feishu["table_id"] = (
        feishu.get("table_id") or os.environ.get("BITABLE_TABLE_ID") or os.environ.get("COMMIT_TABLE_ID") or ""
    )

    # 校验必填字段
    _validate_feishu(feishu)

    return config


def _validate_feishu(feishu: dict):
    """校验飞书必填配置。"""
    required = {
        "app_id": "FEISHU_APP_ID",
        "app_secret": "FEISHU_APP_SECRET",
        "app_token": "BITABLE_APP_TOKEN",
        "table_id": "BITABLE_TABLE_ID",
    }
    missing = [env_hint for key, env_hint in required.items() if not feishu.get(key)]
    if missing:
        print(f"错误: 缺少飞书配置（config.yaml 或环境变量）: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def get_field_name(config: dict, logical_name: str) -> str:
    """
    通过逻辑名获取实际飞书字段名。

    例: get_field_name(config, "trace_file") → "Trace文件"
    """
    mapping = config.get("field_mapping", {})
    name = mapping.get(logical_name)
    if name is None:
        raise KeyError(f"字段映射中未找到逻辑名: {logical_name}")
    return name
