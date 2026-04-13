#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
可扩展性测试

模拟创建一个全新项目 "code_quality_check"，验证:
1. 不修改 core/ 任何代码就能运行新项目
2. 不同的字段映射、阈值、阶段数都能正确工作
3. 新项目的脚本能正确加载自己的 config 并使用 core 组件
"""

import json
import os
import sys
import tempfile
import textwrap
import unittest

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestExtensibility(unittest.TestCase):
    """验证框架对新项目的可扩展性"""

    def _create_project(self, project_name, config_dict, scripts=None):
        """在临时目录中创建项目"""
        tmpdir = tempfile.mkdtemp()
        project_dir = os.path.join(tmpdir, "projects", project_name)
        os.makedirs(project_dir)

        # 写 config.yaml
        with open(os.path.join(project_dir, "config.yaml"), "w") as f:
            yaml.dump(config_dict, f, allow_unicode=True)

        # 写脚本
        if scripts:
            for name, code in scripts.items():
                with open(os.path.join(project_dir, name), "w") as f:
                    f.write(code)

        return tmpdir, project_dir

    def test_new_project_with_different_field_mapping(self):
        """新项目使用完全不同的字段映射"""
        from core.config_loader import load_project_config, get_field_name

        config = {
            "project": {"name": "code_quality_check", "description": "代码质量检查"},
            "feishu": {
                "app_id": "new_id", "app_secret": "new_secret",
                "app_token": "new_token", "table_id": "new_table",
            },
            "field_mapping": {
                "repo_url": "仓库地址",
                "branch": "分支名",
                "reviewer": "审查人",
                "quality_score": "质量得分",
                "comments": "审查意见",
            },
            "stages": [
                {"name": "lint", "script": "lint.py"},
                {"name": "security_scan", "script": "scan.py"},
            ],
        }
        _, project_dir = self._create_project("code_quality_check", config)

        loaded = load_project_config(project_dir)

        self.assertEqual(loaded["project"]["name"], "code_quality_check")
        self.assertEqual(get_field_name(loaded, "repo_url"), "仓库地址")
        self.assertEqual(get_field_name(loaded, "quality_score"), "质量得分")
        self.assertEqual(len(loaded["stages"]), 2)
        self.assertEqual(loaded["stages"][0]["name"], "lint")

    def test_new_project_with_custom_scoring(self):
        """新项目使用 4 个评分维度（不同于 expert_review 的 3 个）"""
        from core.config_loader import load_project_config

        config = {
            "project": {"name": "design_review"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "scoring": {
                "pass_threshold": 8,
                "review_threshold": 6,
                "dimensions": [
                    {"key": "aesthetics", "max_score": 3},
                    {"key": "usability", "max_score": 3},
                    {"key": "accessibility", "max_score": 2},
                    {"key": "innovation", "max_score": 2},
                ],
            },
            "stages": [],
            "field_mapping": {},
        }
        _, project_dir = self._create_project("design_review", config)
        loaded = load_project_config(project_dir)

        dims = loaded["scoring"]["dimensions"]
        self.assertEqual(len(dims), 4)
        total_max = sum(d["max_score"] for d in dims)
        self.assertEqual(total_max, 10)
        self.assertEqual(loaded["scoring"]["pass_threshold"], 8)

    def test_new_project_feishu_client_isolation(self):
        """两个项目的 FeishuClient 不互相污染"""
        from core.feishu_utils import FeishuClient

        client_a = FeishuClient("id_a", "secret_a", "token_a", "table_a")
        client_b = FeishuClient("id_b", "secret_b", "token_b", "table_b")

        self.assertEqual(client_a.app_token, "token_a")
        self.assertEqual(client_b.app_token, "token_b")
        self.assertNotEqual(client_a.table_id, client_b.table_id)

    def test_new_project_pipeline_runs(self):
        """新项目只有 1 个 stage，pipeline_runner 能正确执行"""
        from core.pipeline_runner import run_pipeline

        config = {
            "project": {"name": "simple_check"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "stages": [
                {"name": "check", "script": "check.py", "exit_code_handling": {0: "continue"}},
            ],
            "field_mapping": {},
        }
        script_code = textwrap.dedent("""\
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument('--record-id')
            p.add_argument('--project-dir')
            args = p.parse_args()
            print(f"running check for {args.record_id}")
        """)
        _, project_dir = self._create_project("simple_check", config, {"check.py": script_code})

        result = run_pipeline(project_dir, "new_record_456")
        self.assertEqual(result, 0)

    def test_new_project_5_stages(self):
        """新项目有 5 个阶段，框架能支持"""
        from core.pipeline_runner import run_pipeline

        script_code = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()"

        config = {
            "project": {"name": "five_stages"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "stages": [
                {"name": f"stage{i}", "script": f"s{i}.py", "exit_code_handling": {0: "continue"}}
                for i in range(5)
            ],
            "field_mapping": {},
        }
        scripts = {f"s{i}.py": script_code for i in range(5)}
        _, project_dir = self._create_project("five_stages", config, scripts)

        result = run_pipeline(project_dir, "test")
        self.assertEqual(result, 0)

    def test_writeback_with_custom_dimensions(self):
        """writeback 的 extract_scores 能适配不同维度配置"""
        from projects.expert_review.writeback import extract_scores

        # 4 维度的评审结果（使用新 module_key 参数）
        custom_dims = [
            {"key": "aesthetics", "max_score": 3},
            {"key": "usability", "max_score": 3},
            {"key": "accessibility", "max_score": 2},
            {"key": "innovation", "max_score": 2},
        ]
        ai_result = {
            "design_quality": {
                "aesthetics": {"score": 2},
                "usability": {"score": 3},
                "accessibility": {"score": 1},
                "innovation": {"score": 2},
            }
        }
        scores = extract_scores(ai_result, "design_quality", custom_dims)
        self.assertEqual(scores["aesthetics"], 2)
        self.assertEqual(scores["usability"], 3)
        self.assertEqual(scores["accessibility"], 1)
        self.assertEqual(scores["innovation"], 2)
        self.assertEqual(scores["total"], 8)

    def test_core_modules_no_global_state(self):
        """core 模块不依赖全局模块变量（无 check_required_env 等残留）"""
        import core.feishu_utils as fu
        import core.config_loader as cl
        import core.trace_parser as tp

        # feishu_utils 中不应有模块级的 FEISHU_APP_ID 等全局变量
        self.assertFalse(hasattr(fu, 'FEISHU_APP_ID'))
        self.assertFalse(hasattr(fu, 'BITABLE_APP_TOKEN'))
        self.assertFalse(hasattr(fu, 'check_required_env'))

        # trace_parser 应该只导出类和函数，不依赖全局状态
        self.assertTrue(hasattr(tp, 'TraceAnalysis'))
        self.assertTrue(hasattr(tp, 'parse_trace_file'))

    def test_daytona_runner_config_customizable(self):
        """DaytonaRunConfig 支持不同项目的自定义配置"""
        from core.daytona_runner import DaytonaRunConfig

        config_a = DaytonaRunConfig(
            snapshot="custom-snapshot",
            cpu=4, memory=8, disk=20,
            model="anthropic/claude-opus-4-20250514",
            timeout=1200,
        )
        config_b = DaytonaRunConfig(
            snapshot="daytona-small",
            cpu=1, memory=2, disk=5,
            model="anthropic/claude-haiku-4-5-20251001",
            timeout=120,
        )

        self.assertEqual(config_a.cpu, 4)
        self.assertEqual(config_b.cpu, 1)
        self.assertEqual(config_a.timeout, 1200)
        self.assertNotEqual(config_a.model, config_b.model)


if __name__ == "__main__":
    unittest.main()
