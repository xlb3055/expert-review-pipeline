#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
通用 Processor 框架

提供 ProcessorContext（阶段间共享数据）、BaseProcessor 基类、
注册/查找机制，让 pipeline_runner 通过 processor 名称实例化并执行。
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from core.feishu_utils import FeishuClient


# ========== 全局注册表 ==========

_REGISTRY: dict = {}


def register(name: str):
    """装饰器：将 Processor 类注册到全局注册表。"""
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_processor(name: str):
    """按名称查找已注册的 Processor 类。"""
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"未找到 Processor: {name}（已注册: {list(_REGISTRY.keys())}）")
    return cls


# ========== ProcessorContext ==========

@dataclass
class ProcessorContext:
    """各 Processor 共享的运行上下文。"""
    record_id: str = ""
    project_dir: str = ""
    config: dict = field(default_factory=dict)
    client: Optional[FeishuClient] = None
    app_token: str = ""
    table_id: str = ""
    data: dict = field(default_factory=dict)
    workspace_dir: str = ""

    @classmethod
    def from_config(cls, config: dict, record_id: str, project_dir: str) -> "ProcessorContext":
        """从已加载的 config 构造 Context。"""
        feishu = config.get("feishu", {})
        client = FeishuClient.from_config(config)
        workspace = config.get("workspace", {})
        workspace_dir = os.environ.get(
            "WORKSPACE_DIR",
            workspace.get("base_dir", "/workspace"),
        )
        os.makedirs(workspace_dir, exist_ok=True)
        return cls(
            record_id=record_id,
            project_dir=project_dir,
            config=config,
            client=client,
            app_token=feishu.get("app_token", ""),
            table_id=feishu.get("table_id", ""),
            data={},
            workspace_dir=workspace_dir,
        )


# ========== BaseProcessor ==========

class BaseProcessor:
    """Processor 基类，所有内置/自定义 Processor 继承此类。"""

    def __init__(self, stage_config: dict):
        """
        stage_config: stages 列表中当前阶段的完整字典，
        包括 name, processor, config, exit_code_handling 等。
        """
        self.stage_config = stage_config
        self.name = stage_config.get("name", "")
        self.processor_config = stage_config.get("config", {})

    def run(self, ctx: ProcessorContext) -> int:
        """
        执行 Processor 逻辑。

        返回退出码: 0=成功, 其他值由 exit_code_handling 决定后续行为。
        """
        raise NotImplementedError("子类必须实现 run()")


# ========== 自动导入内置 Processor ==========
# 触发 @register 装饰器执行

def _import_builtins():
    """导入所有内置 Processor 模块，触发注册。"""
    from core.processors import feishu_fetch      # noqa: F401
    from core.processors import feishu_writeback   # noqa: F401


_import_builtins()
