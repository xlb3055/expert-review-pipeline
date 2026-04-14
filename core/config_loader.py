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

    从 config["field_mapping"] 中查找。
    例: get_field_name(config, "task_description") → "任务说明"
    """
    mapping = config.get("field_mapping", {})
    name = mapping.get(logical_name)
    if name is None:
        raise KeyError(f"字段映射中未找到逻辑名: {logical_name}")
    return name
