#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pipeline_runner 编排测试 + 业务脚本端到端测试（mock Feishu）

验证:
1. pipeline_runner 能正确读 config、按序执行 stage、处理退出码
2. pre_screen 的 7 项硬门槛检查逻辑
3. writeback 的双模块评分提取和结论判定逻辑
4. ai_review 的输入组装
"""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ============================================================
# Part 1: pipeline_runner 编排测试
# ============================================================

class TestPipelineRunner(unittest.TestCase):
    """测试 pipeline_runner 的阶段执行和退出码处理逻辑"""

    def _make_project(self, stages, scripts=None):
        """
        创建一个临时项目目录，包含 config.yaml 和可选的 stage 脚本。
        scripts: dict[filename -> python code string]
        """
        import yaml
        tmpdir = tempfile.mkdtemp()
        workspace = tempfile.mkdtemp()

        config = {
            "project": {"name": "test_pipeline"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "stages": stages,
            "field_mapping": {},
            "workspace": {"base_dir": workspace},
        }
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config, f, allow_unicode=True)

        if scripts:
            for name, code in scripts.items():
                with open(os.path.join(tmpdir, name), "w") as f:
                    f.write(code)

        return tmpdir

    def test_all_stages_pass(self):
        """三个阶段都返回 0，流水线应成功"""
        from core.pipeline_runner import run_pipeline

        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(0)",
            "stage2.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(0)",
            "stage3.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(0)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
            {"name": "s3", "script": "stage3.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 0)

    def test_stop_action_skips_remaining(self):
        """stage1 返回 1 → stop，不执行 stage2"""
        from core.pipeline_runner import run_pipeline

        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(1)",
            "stage2.py": "import sys; sys.exit(99)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue", 1: "stop"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 0)

    def test_error_action_fails_pipeline(self):
        """stage1 返回非预期退出码，默认策略 → error"""
        from core.pipeline_runner import run_pipeline

        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(3)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)

    def test_continue_after_non_zero(self):
        """stage1 返回 1 但配置为 continue，stage2 应该执行"""
        from core.pipeline_runner import run_pipeline

        marker = tempfile.mktemp()
        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nsys.exit(1)",
            "stage2.py": f"import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.add_argument('--ctx-data-file', required=False)\np.parse_args()\nopen('{marker}', 'w').write('executed')\nsys.exit(0)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue", 1: "continue"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 0)
        self.assertTrue(os.path.exists(marker), "stage2 应该被执行")
        os.unlink(marker)

    def test_missing_script_fails(self):
        """引用不存在的脚本应失败"""
        from core.pipeline_runner import run_pipeline

        stages = [
            {"name": "missing", "script": "nonexistent.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages)
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)

    def test_no_stages_fails(self):
        """config 中无 stages 应返回错误"""
        from core.pipeline_runner import run_pipeline
        tmpdir = self._make_project([])
        # no_stages 会在 ctx 创建前就返回 1
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)

    def test_stage_receives_correct_args(self):
        """验证 pipeline_runner 传递 --record-id, --project-dir, --ctx-data-file"""
        marker = tempfile.mktemp()
        script_code = textwrap.dedent(f"""\
            import argparse, json
            p = argparse.ArgumentParser()
            p.add_argument('--record-id')
            p.add_argument('--project-dir')
            p.add_argument('--ctx-data-file')
            args = p.parse_args()
            with open('{marker}', 'w') as f:
                json.dump({{
                    'record_id': args.record_id,
                    'project_dir': args.project_dir,
                    'has_ctx_data': args.ctx_data_file is not None,
                }}, f)
        """)
        stages = [{"name": "check_args", "script": "check.py", "exit_code_handling": {0: "continue"}}]
        tmpdir = self._make_project(stages, {"check.py": script_code})

        from core.pipeline_runner import run_pipeline
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = MagicMock()
            run_pipeline(tmpdir, "my_record_123")

        with open(marker) as f:
            data = json.load(f)
        self.assertEqual(data["record_id"], "my_record_123")
        self.assertEqual(data["project_dir"], tmpdir)
        self.assertTrue(data["has_ctx_data"])
        os.unlink(marker)


# ============================================================
# Part 2: pre_screen 7 项硬门槛测试
# ============================================================

class TestPreScreenLogic(unittest.TestCase):
    """测试 pre_screen.py 的 7 项检查函数"""

    # --- 检查 1: task_authenticity ---

    def test_task_authenticity_pass(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("这是一段足够长的任务描述，涉及真实的业务场景和技术实现细节")
        self.assertTrue(result["passed"])

    def test_task_authenticity_empty(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_task_authenticity_demo_rejected(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("hello world")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_task_authenticity_test_keyword_rejected(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("测试")
        self.assertFalse(result["passed"])

    # --- 检查 2: trace_integrity ---

    def test_trace_integrity_pass(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(is_valid=True, conversation_rounds=5)
        result = check_trace_integrity(True, trace, 3)
        self.assertTrue(result["passed"])

    def test_trace_integrity_no_attachment(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis()
        result = check_trace_integrity(False, trace, 3)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_trace_integrity_invalid_trace(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(is_valid=False, errors=["解析失败"])
        result = check_trace_integrity(True, trace, 3)
        self.assertFalse(result["passed"])

    def test_trace_integrity_low_rounds(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(is_valid=True, conversation_rounds=1)
        result = check_trace_integrity(True, trace, 3)
        self.assertFalse(result["passed"])

    # --- 检查 3: tool_loop_exists ---

    def test_tool_loop_exists_pass(self):
        from projects.expert_review.pre_screen import check_tool_loop_exists
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(has_tool_calls=True, tool_call_count=5)
        result = check_tool_loop_exists(trace)
        self.assertTrue(result["passed"])

    def test_tool_loop_exists_fail(self):
        from projects.expert_review.pre_screen import check_tool_loop_exists
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(has_tool_calls=False, tool_call_count=0)
        result = check_tool_loop_exists(trace)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    # --- 检查 4: final_product_exists ---

    def test_final_product_link(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists({"link": "https://example.com"})
        self.assertTrue(result["passed"])

    def test_final_product_attachment(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists([{"file_token": "xyz"}])
        self.assertTrue(result["passed"])

    def test_final_product_missing(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists(None)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    # --- 检查 5: verification_exists ---

    def test_verification_exists_bash_tool(self):
        from projects.expert_review.pre_screen import check_verification_exists
        result = check_verification_exists("[工具调用] Bash: pytest tests/")
        self.assertTrue(result["passed"])

    def test_verification_exists_no_bash(self):
        from projects.expert_review.pre_screen import check_verification_exists
        result = check_verification_exists("[工具调用] Read: /tmp/file.py")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_verification_exists_code_content_not_matched(self):
        from projects.expert_review.pre_screen import check_verification_exists
        result = check_verification_exists("run the test suite to verify")
        self.assertFalse(result["passed"])

    # --- 检查 6: trace_product_consistent ---

    def test_trace_product_consistent_pass(self):
        from projects.expert_review.pre_screen import check_trace_product_consistent
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(is_valid=True)
        result = check_trace_product_consistent(trace, True)
        self.assertTrue(result["passed"])

    def test_trace_product_consistent_no_product(self):
        from projects.expert_review.pre_screen import check_trace_product_consistent
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis(is_valid=True)
        result = check_trace_product_consistent(trace, False)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    # --- 检查 7: compliance_check ---

    def test_compliance_pass(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("请帮我实现一个缓存系统")
        self.assertTrue(result["passed"])

    def test_compliance_secret_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("using key sk-abcdefghijklmnopqrstuvwxyz1234567890 for auth")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_compliance_aws_key_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("AKIAIOSFODNN7EXAMPLE in config")
        self.assertFalse(result["passed"])

    def test_compliance_code_variable_not_flagged(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance('const token = localStorage.getItem("auth_token");')
        self.assertTrue(result["passed"])

    def test_compliance_private_key_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("-----BEGIN PRIVATE KEY-----")
        self.assertFalse(result["passed"])


# ============================================================
# Part 3: writeback 双模块评分和结论测试
# ============================================================

class TestWritebackLogic(unittest.TestCase):
    """测试 writeback.py 的双模块评分提取和结论判定"""

    def _expert_dims(self):
        return [
            {"key": "task_complexity", "max_score": 3},
            {"key": "iteration_quality", "max_score": 3},
            {"key": "professional_judgment", "max_score": 4},
        ]

    def _trace_dims(self):
        return [
            {"key": "authenticity", "max_score": 2},
            {"key": "info_density", "max_score": 2},
            {"key": "tool_loop", "max_score": 2},
            {"key": "correction_value", "max_score": 2},
            {"key": "verification_loop", "max_score": 2},
            {"key": "compliance", "max_score": 2},
        ]

    def test_extract_expert_scores(self):
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 2, "evidence": "..."},
                "iteration_quality": {"score": 3, "evidence": "..."},
                "professional_judgment": {"score": 3, "evidence": "..."},
                "total": 8,
            }
        }
        scores = extract_scores(ai_result, "expert_ability", self._expert_dims())
        self.assertEqual(scores["task_complexity"], 2)
        self.assertEqual(scores["iteration_quality"], 3)
        self.assertEqual(scores["professional_judgment"], 3)
        self.assertEqual(scores["total"], 8)

    def test_extract_trace_scores(self):
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "trace_asset": {
                "authenticity": {"score": 2, "evidence": "..."},
                "info_density": {"score": 1, "evidence": "..."},
                "tool_loop": {"score": 2, "evidence": "..."},
                "correction_value": {"score": 1, "evidence": "..."},
                "verification_loop": {"score": 2, "evidence": "..."},
                "compliance": {"score": 2, "evidence": "..."},
                "total": 10,
            }
        }
        scores = extract_scores(ai_result, "trace_asset", self._trace_dims())
        self.assertEqual(scores["authenticity"], 2)
        self.assertEqual(scores["info_density"], 1)
        self.assertEqual(scores["total"], 10)

    def test_extract_scores_clamped(self):
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 10},
                "iteration_quality": {"score": -1},
                "professional_judgment": {"score": 4},
            }
        }
        scores = extract_scores(ai_result, "expert_ability", self._expert_dims())
        self.assertEqual(scores["task_complexity"], 3)
        self.assertEqual(scores["iteration_quality"], 0)
        self.assertEqual(scores["professional_judgment"], 4)

    def test_extract_scores_direct_numbers(self):
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": 2,
                "iteration_quality": 2,
                "professional_judgment": 3,
            }
        }
        scores = extract_scores(ai_result, "expert_ability", self._expert_dims())
        self.assertEqual(scores["total"], 7)

    def test_extract_scores_empty(self):
        from projects.expert_review.writeback import extract_scores
        scores = extract_scores({}, "expert_ability", self._expert_dims())
        self.assertEqual(scores["total"], 0)

    def test_conclusion_pass(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(8, 10, "通过", pass_score=70)
        self.assertEqual(conclusion, "通过")
        self.assertGreaterEqual(score, 70)

    def test_conclusion_fail(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(3, 4, "通过", pass_score=70)
        self.assertEqual(conclusion, "不通过")
        self.assertLess(score, 70)

    def test_conclusion_pre_screen_reject(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(10, 12, "拒绝")
        self.assertEqual(conclusion, "不通过")
        self.assertEqual(score, 0.0)

    def test_composite_score(self):
        from projects.expert_review.writeback import compute_composite_score
        score = compute_composite_score(8, 10, 10, 12)
        self.assertAlmostEqual(score, 81.7, places=1)

    def test_composite_score_zero(self):
        from projects.expert_review.writeback import compute_composite_score
        self.assertEqual(compute_composite_score(0, 0, 10, 12), 0.0)


# ============================================================
# Part 4: ai_review 输入组装测试
# ============================================================

class TestAIReviewInputBuild(unittest.TestCase):
    """测试 ai_review.py 的 _build_input_text"""

    def test_build_input_text(self):
        from projects.expert_review.ai_review import _build_input_text
        ctx_data = {
            "task_description": "实现一个分布式缓存系统",
            "expert_name": "张三",
            "expert_id": "12345",
            "position": "Coding",
            "_raw_final_product": {"link": "https://github.com/example"},
        }
        trace_content = '{"type":"human","content":"你好"}'

        text = _build_input_text(ctx_data, trace_content)

        self.assertIn("张三", text)
        self.assertIn("12345", text)
        self.assertIn("Coding", text)
        self.assertIn("分布式缓存系统", text)
        self.assertIn("https://github.com/example", text)
        self.assertIn("Trace 日志", text)
        self.assertIn("你好", text)

    def test_build_input_text_no_product_link(self):
        from projects.expert_review.ai_review import _build_input_text
        ctx_data = {
            "task_description": "test",
            "expert_name": "李四",
        }
        text = _build_input_text(ctx_data, "trace...")
        self.assertIn("李四", text)
        self.assertIn("trace...", text)


if __name__ == "__main__":
    unittest.main()
