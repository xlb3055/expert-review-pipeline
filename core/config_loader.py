#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
配置加载器

加载项目 config.yaml，合并环境变量，提供字段映射查询。
支持新格式 data_source.fields / data_sink.field_mapping 以及旧格式 field_mapping。
"""

import os
import sys

import yaml


def load_project_config(project_dir: str) -> dict:
    """
    加载项目目录下的 config.yaml。

    只合并飞书凭证（app_id/app_secret）环境变量。
    不校验 app_token / table_id — 那是每个项目自己的事。
    """
    config_path = os.path.join(project_dir, "config.yaml")
    if not os.path.isfile(config_path):
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # 合并飞书凭证：config 优先，空值回退环境变量
    feishu = config.setdefault("feishu", {})
    feishu["app_id"] = (
        feishu.get("app_id") or os.environ.get("FEISHU_APP_ID") or os.environ.get("APP_ID") or ""
    )
    feishu["app_secret"] = (
        feishu.get("app_secret") or os.environ.get("FEISHU_APP_SECRET") or os.environ.get("APP_SECRET") or ""
    )

    # app_token / table_id：合并环境变量，但不强制校验
    feishu["app_token"] = (
        feishu.get("app_token") or os.environ.get("BITABLE_APP_TOKEN") or ""
    )
    feishu["table_id"] = (
        feishu.get("table_id") or os.environ.get("BITABLE_TABLE_ID") or ""
    )

    # 只校验凭证
    _validate_feishu(feishu)

    return config


def _validate_feishu(feishu: dict):
    """校验飞书凭证（只校验 app_id/app_secret）。"""
    required = {
        "app_id": "FEISHU_APP_ID",
        "app_secret": "FEISHU_APP_SECRET",
    }
    missing = [env_hint for key, env_hint in required.items() if not feishu.get(key)]
    if missing:
        print(f"错误: 缺少飞书凭证（config.yaml 或环境变量）: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def get_field_name(config: dict, logical_name: str) -> str:
    """
    通过逻辑名获取实际飞书字段名。

    查找顺序: data_source.fields → field_mapping
    例: get_field_name(config, "task_description") → "任务说明"
    """
    # 优先从 data_source.fields 查找
    ds = config.get("data_source", {})
    ds_fields = ds.get("fields", {})
    if logical_name in ds_fields:
        return ds_fields[logical_name]

    # 回退到旧的 field_mapping
    mapping = config.get("field_mapping", {})
    name = mapping.get(logical_name)
    if name is None:
        raise KeyError(f"字段映射中未找到逻辑名: {logical_name}")
    return name


def get_sink_field_name(config: dict, result_key: str) -> str:
    """
    通过结果键获取飞书回填字段名。

    查找顺序: data_sink.field_mapping → field_mapping
    例: get_sink_field_name(config, "review_status") → "审核状态"
    """
    # 优先从 data_sink.field_mapping 查找
    sink = config.get("data_sink", {})
    sink_mapping = sink.get("field_mapping", {})
    if result_key in sink_mapping:
        return sink_mapping[result_key]

    # 回退到旧的 field_mapping
    mapping = config.get("field_mapping", {})
    name = mapping.get(result_key)
    if name is None:
        raise KeyError(f"输出字段映射中未找到: {result_key}")
    return name
