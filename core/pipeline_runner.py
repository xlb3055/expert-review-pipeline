#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
通用流水线执行器

读取项目 config.yaml 中的 stages 列表，按序执行各阶段脚本，
根据 exit_code_handling 映射决定 continue / stop / error。
"""

import argparse
import os
import subprocess
import sys
import time

from core.config_loader import load_project_config


def run_pipeline(project_dir: str, record_id: str) -> int:
    """
    执行流水线。

    读取 project_dir/config.yaml → 按 stages 顺序执行脚本。
    每个 stage 以 python3 <script> --record-id <id> --project-dir <dir> 调用。

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

    for i, stage in enumerate(stages):
        stage_name = stage.get("name", f"stage_{i}")
        script = stage.get("script", "")
        description = stage.get("description", stage_name)
        exit_code_handling = stage.get("exit_code_handling", {})

        if not script:
            print(f"警告: 阶段 {stage_name} 未指定 script，跳过")
            continue

        script_path = os.path.join(project_dir, script)
        if not os.path.isfile(script_path):
            print(f"错误: 脚本不存在: {script_path}", file=sys.stderr)
            return 1

        print(f"\n{'='*50}")
        stage_start = time.time()
        print(f"===== 阶段 {i+1}/{len(stages)}: {description} =====")

        cmd = [
            sys.executable, script_path,
            "--record-id", record_id,
            "--project-dir", project_dir,
        ]

        try:
            result = subprocess.run(cmd, cwd=project_dir)
            exit_code = result.returncode
        except Exception as e:
            print(f"阶段 {stage_name} 执行异常: {e}", file=sys.stderr)
            exit_code = 99

        stage_elapsed = time.time() - stage_start
        print(f"阶段 {stage_name} 退出码: {exit_code}, 耗时: {stage_elapsed:.1f}s")

        # 解析退出码处理策略
        action = exit_code_handling.get(exit_code, exit_code_handling.get(str(exit_code)))

        if action is None:
            # 默认策略：0=continue, 其他=error
            if exit_code == 0:
                action = "continue"
            else:
                action = "error"

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
