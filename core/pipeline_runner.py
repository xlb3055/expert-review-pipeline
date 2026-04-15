#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
通用流水线执行器

读取项目 config.yaml 中的 stages 列表，按序执行各阶段。
支持两种执行模式：
- processor 模式：通过注册的 Processor 类执行（配置驱动）
- script 模式：子进程执行 Python 脚本（向后兼容）
"""

import argparse
import json
import os
import subprocess
import sys
import time

from core.config_loader import load_project_config


def run_pipeline(project_dir: str, record_id: str) -> int:
    """
    执行流水线。

    读取 project_dir/config.yaml → 按 stages 顺序执行。
    每个 stage 可以指定 processor（类名）或 script（脚本路径）。

    返回: 0=成功, 1=失败
    """
    project_dir = os.path.abspath(project_dir)
    config = load_project_config(project_dir)

    project_name = config.get("project", {}).get("name", os.path.basename(project_dir))
    stages = config.get("stages", [])

    if not stages:
        print("错误: config.yaml 中未定义 stages", file=sys.stderr)
        return 1

    pipeline_start = time.time()
    print(f"===== 流水线开始: {project_name} =====")
    print(f"Record ID: {record_id}")
    print(f"项目目录: {project_dir}")
    print(f"阶段数: {len(stages)}")
    print(f"开始时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 始终创建 Context（processor 和 script 都需要）
    from core.processors import ProcessorContext
    ctx = ProcessorContext.from_config(config, record_id, project_dir)

    for i, stage in enumerate(stages):
        stage_name = stage.get("name", f"stage_{i}")
        processor_name = stage.get("processor")
        script = stage.get("script", "")
        description = stage.get("description", stage_name)
        exit_code_handling = stage.get("exit_code_handling", {})

        print(f"\n{'='*50}")
        stage_start = time.time()
        print(f"===== 阶段 {i+1}/{len(stages)}: {description} =====")

        if processor_name:
            # ===== Processor 模式 =====
            exit_code = _run_processor(processor_name, stage, ctx)
        elif script:
            # ===== Script 模式：通过 ctx_data.json 传递数据 =====
            exit_code = _run_script(script, project_dir, record_id, ctx)
        else:
            print(f"警告: 阶段 {stage_name} 未指定 processor 或 script，跳过")
            continue

        stage_elapsed = time.time() - stage_start
        print(f"阶段 {stage_name} 退出码: {exit_code}, 耗时: {stage_elapsed:.1f}s")

        # 解析退出码处理策略
        action = _resolve_action(exit_code_handling, exit_code)

        if action == "continue":
            print(f"→ 继续下一阶段")
        elif action == "stop":
            print(f"→ 流水线正常结束（不继续后续阶段）")
            break
        elif action == "error":
            print(f"→ 流水线异常终止", file=sys.stderr)
            return 1
        else:
            print(f"→ 未知 action: {action}，视为 continue")

    pipeline_elapsed = time.time() - pipeline_start
    print(f"\n{'='*50}")
    print(f"===== 流水线完成: {project_name} =====")
    print(f"总耗时: {pipeline_elapsed:.1f}s")
    print(f"结束时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    return 0


def _run_processor(processor_name: str, stage_config: dict, ctx) -> int:
    """实例化并运行 Processor，返回退出码。"""
    from core.processors import get_processor

    try:
        cls = get_processor(processor_name)
        processor = cls(stage_config)
        exit_code = processor.run(ctx)
    except Exception as e:
        print(f"Processor {processor_name} 执行异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 99

    return exit_code


def _run_script(script: str, project_dir: str, record_id: str, ctx=None) -> int:
    """子进程执行 Python 脚本，通过 ctx_data.json 传递数据。"""
    script_path = os.path.join(project_dir, script)
    if not os.path.isfile(script_path):
        print(f"错误: 脚本不存在: {script_path}", file=sys.stderr)
        return 99

    # 写出 ctx_data.json 供脚本读取
    ctx_data_file = None
    if ctx is not None:
        ctx_data_file = os.path.join(ctx.workspace_dir, "ctx_data.json")
        with open(ctx_data_file, "w", encoding="utf-8") as f:
            json.dump(ctx.data, f, ensure_ascii=False, indent=2)
        print(f"ctx_data.json 已写出: {ctx_data_file} ({len(ctx.data)} 个键)")

    cmd = [
        sys.executable, script_path,
        "--record-id", record_id,
        "--project-dir", project_dir,
    ]
    if ctx_data_file:
        cmd.extend(["--ctx-data-file", ctx_data_file])

    try:
        result = subprocess.run(cmd, cwd=project_dir)
    except Exception as e:
        print(f"脚本执行异常: {e}", file=sys.stderr)
        return 99

    # 执行后读回 ctx_data.json 合并到 ctx.data
    if ctx is not None and ctx_data_file and os.path.isfile(ctx_data_file):
        try:
            with open(ctx_data_file, "r", encoding="utf-8") as f:
                updated = json.load(f)
            ctx.data.update(updated)
            print(f"ctx_data.json 已读回: {len(updated)} 个键")
        except Exception as e:
            print(f"警告: 读回 ctx_data.json 失败: {e}", file=sys.stderr)

    return result.returncode


def _resolve_action(exit_code_handling: dict, exit_code: int) -> str:
    """根据退出码查找处理策略。"""
    action = exit_code_handling.get(exit_code, exit_code_handling.get(str(exit_code)))
    if action is None:
        if exit_code == 0:
            action = "continue"
        else:
            action = "error"
    return action


def main():
    parser = argparse.ArgumentParser(description="通用流水线执行器")
    parser.add_argument("--project-dir", required=True, help="项目目录路径")
    parser.add_argument("--record-id", required=True, help="飞书多维表格 record_id")
    args = parser.parse_args()

    try:
        exit_code = run_pipeline(args.project_dir, args.record_id)
    except Exception as e:
        print(f"系统错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
