#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ctx_data 读写工具

业务脚本通过 --ctx-data-file 参数拿到 ctx_data.json 路径，
用 load_ctx_data / save_ctx_data 完成数据交换。
"""

import json


def load_ctx_data(path: str) -> dict:
    """从 ctx_data.json 加载数据。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ctx_data(path: str, data: dict):
    """把处理结果写回 ctx_data.json。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
