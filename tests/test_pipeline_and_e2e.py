#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pipeline_runner 编排测试 + 业务脚本端到端测试（mock Feishu）

验证:
1. pipeline_runner 能正确读 config、按序执行 stage、处理退出码
2. pre_screen 的 7 项硬门槛检查逻辑在 mock 数据下能完整跑通
3. writeback 的双模块评分提取和新结论判定逻辑正确
4. ai_review 的输入组装和 config 读取正确
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

        config = {
            "project": {"name": "test_pipeline"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "stages": stages,
            "field_mapping": {},
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
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(0)",
            "stage2.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(0)",
            "stage3.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(0)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
            {"name": "s3", "script": "stage3.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 0)

    def test_stop_action_skips_remaining(self):
        """stage1 返回 1 → stop，不执行 stage2"""
        from core.pipeline_runner import run_pipeline

        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(1)",
            "stage2.py": "import sys; sys.exit(99)",  # 不应被执行
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue", 1: "stop"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 0)  # stop 是正常结束

    def test_error_action_fails_pipeline(self):
        """stage1 返回非预期退出码，默认策略 → error"""
        from core.pipeline_runner import run_pipeline

        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(3)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue"}},
            # exit_code 3 没有映射 → 走默认 error
        ]
        tmpdir = self._make_project(stages, scripts)
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)  # 流水线失败

    def test_continue_after_non_zero(self):
        """stage1 返回 1 但配置为 continue，stage2 应该执行"""
        from core.pipeline_runner import run_pipeline

        # 用 marker 文件证明 stage2 被执行了
        marker = tempfile.mktemp()
        scripts = {
            "stage1.py": "import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nsys.exit(1)",
            "stage2.py": f"import sys, argparse\np = argparse.ArgumentParser()\np.add_argument('--record-id')\np.add_argument('--project-dir')\np.parse_args()\nopen('{marker}', 'w').write('executed')\nsys.exit(0)",
        }
        stages = [
            {"name": "s1", "script": "stage1.py", "exit_code_handling": {0: "continue", 1: "continue"}},
            {"name": "s2", "script": "stage2.py", "exit_code_handling": {0: "continue"}},
        ]
        tmpdir = self._make_project(stages, scripts)
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
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)

    def test_no_stages_fails(self):
        """config 中无 stages 应返回错误"""
        from core.pipeline_runner import run_pipeline
        tmpdir = self._make_project([])
        result = run_pipeline(tmpdir, "test_record")
        self.assertEqual(result, 1)

    def test_stage_receives_correct_args(self):
        """验证 pipeline_runner 传递的 --record-id 和 --project-dir 正确"""
        marker = tempfile.mktemp()
        script_code = textwrap.dedent(f"""\
            import argparse, json
            p = argparse.ArgumentParser()
            p.add_argument('--record-id')
            p.add_argument('--project-dir')
            args = p.parse_args()
            with open('{marker}', 'w') as f:
                json.dump({{'record_id': args.record_id, 'project_dir': args.project_dir}}, f)
        """)
        stages = [{"name": "check_args", "script": "check.py", "exit_code_handling": {0: "continue"}}]
        tmpdir = self._make_project(stages, {"check.py": script_code})

        from core.pipeline_runner import run_pipeline
        run_pipeline(tmpdir, "my_record_123")

        with open(marker) as f:
            data = json.load(f)
        self.assertEqual(data["record_id"], "my_record_123")
        self.assertEqual(data["project_dir"], tmpdir)
        os.unlink(marker)


# ============================================================
# Part 2: pre_screen 7 项硬门槛测试
# ============================================================

class TestPreScreenLogic(unittest.TestCase):
    """测试 pre_screen.py 的 7 项检查函数"""

    # --- 检查 1: task_authenticity ---

    def test_task_authenticity_pass(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        fields = {"任务描述": "这是一段足够长的任务描述，涉及真实的业务场景和技术实现细节" * 2}
        result = check_task_authenticity(fields, "任务描述", 50)
        self.assertTrue(result["passed"])

    def test_task_authenticity_too_short(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        fields = {"任务描述": "太短"}
        result = check_task_authenticity(fields, "任务描述", 50)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_task_authenticity_demo_rejected(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        # 内容足够长但是纯 demo 关键词（需要长度也满足才会到关键词检查）
        fields = {"任务描述": "hello world" + " " * 50}
        result = check_task_authenticity(fields, "任务描述", 50)
        # 长度满足后检查内容 — hello world 不会完全匹配因为有后续空格
        # 用精确匹配测试
        fields2 = {"任务描述": "hello world"}
        result2 = check_task_authenticity(fields2, "任务描述", 5)
        self.assertFalse(result2["passed"])
        self.assertEqual(result2["action"], "reject")

    def test_task_authenticity_test_keyword_rejected(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        fields = {"任务描述": "测试"}
        result = check_task_authenticity(fields, "任务描述", 2)
        self.assertFalse(result["passed"])

    # --- 检查 2: trace_integrity ---

    def test_trace_integrity_pass(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        fields = {"Trace文件": [{"file_token": "abc", "name": "trace.jsonl"}]}
        trace = TraceAnalysis(is_valid=True, conversation_rounds=5)
        result = check_trace_integrity(fields, "Trace文件", trace, 3)
        self.assertTrue(result["passed"])

    def test_trace_integrity_no_attachment(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        trace = TraceAnalysis()
        result = check_trace_integrity({}, "Trace文件", trace, 3)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_trace_integrity_invalid_trace(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        fields = {"Trace文件": [{"file_token": "abc"}]}
        trace = TraceAnalysis(is_valid=False, errors=["解析失败"])
        result = check_trace_integrity(fields, "Trace文件", trace, 3)
        self.assertFalse(result["passed"])

    def test_trace_integrity_low_rounds(self):
        from projects.expert_review.pre_screen import check_trace_integrity
        from core.trace_parser import TraceAnalysis
        fields = {"Trace文件": [{"file_token": "abc"}]}
        trace = TraceAnalysis(is_valid=True, conversation_rounds=1)
        result = check_trace_integrity(fields, "Trace文件", trace, 3)
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
        fields = {"最终产物": {"link": "https://example.com"}}
        result = check_final_product_exists(fields, "最终产物", "最终附件")
        self.assertTrue(result["passed"])

    def test_final_product_attachment(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        fields = {"最终附件": [{"file_token": "xyz"}]}
        result = check_final_product_exists(fields, "最终产物", "最终附件")
        self.assertTrue(result["passed"])

    def test_final_product_missing(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists({}, "最终产物", "最终附件")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    # --- 检查 5: verification_exists ---

    def test_verification_exists_bash_tool(self):
        from projects.expert_review.pre_screen import check_verification_exists
        trace = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest tests/"}}]}}'
        result = check_verification_exists(trace)
        self.assertTrue(result["passed"])

    def test_verification_exists_top_level_tool_use(self):
        from projects.expert_review.pre_screen import check_verification_exists
        trace = '{"type":"tool_use","name":"bash","input":{"command":"ls"}}'
        result = check_verification_exists(trace)
        self.assertTrue(result["passed"])

    def test_verification_exists_no_bash(self):
        """只有 Read/Edit/Write 不算验证类"""
        from projects.expert_review.pre_screen import check_verification_exists
        trace = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"path":"/tmp/a.py"}}]}}'
        result = check_verification_exists(trace)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_verification_exists_code_content_not_matched(self):
        """代码内容中含 run/test 等关键词不应误报"""
        from projects.expert_review.pre_screen import check_verification_exists
        trace = '{"type":"assistant","message":{"content":[{"type":"text","text":"run the test suite to verify"}]}}'
        result = check_verification_exists(trace)
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
        trace = '{"type":"human","content":"请帮我实现一个缓存系统"}'
        result = check_compliance(trace)
        self.assertTrue(result["passed"])

    def test_compliance_secret_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        trace = 'using key sk-abcdefghijklmnopqrstuvwxyz1234567890 for auth'
        result = check_compliance(trace)
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_compliance_aws_key_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        trace = 'AKIAIOSFODNN7EXAMPLE in config'
        result = check_compliance(trace)
        self.assertFalse(result["passed"])

    def test_compliance_code_variable_not_flagged(self):
        """代码中的变量赋值不应触发合规性告警"""
        from projects.expert_review.pre_screen import check_compliance
        trace = 'const token = localStorage.getItem("auth_token");'
        result = check_compliance(trace)
        self.assertTrue(result["passed"])

    def test_compliance_private_key_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        trace = '-----BEGIN PRIVATE KEY-----'
        result = check_compliance(trace)
        self.assertFalse(result["passed"])


# ============================================================
# Part 3: writeback 双模块评分和新结论测试
# ============================================================

class TestWritebackLogic(unittest.TestCase):
    """测试 writeback.py 的双模块评分提取和新结论判定"""

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
        """超出范围的分数应被裁剪"""
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 10},   # max 3
                "iteration_quality": {"score": -1},  # min 0
                "professional_judgment": {"score": 4},
            }
        }
        scores = extract_scores(ai_result, "expert_ability", self._expert_dims())
        self.assertEqual(scores["task_complexity"], 3)
        self.assertEqual(scores["iteration_quality"], 0)
        self.assertEqual(scores["professional_judgment"], 4)

    def test_extract_scores_direct_numbers(self):
        """AI 直接返回数值而非 dict"""
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

    # --- 新结论判定逻辑 ---

    def test_conclusion_expert_storable(self):
        """专家能力 >= 7 → 可储备专家"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(8, 5, "通过"), "可储备专家")
        self.assertEqual(determine_conclusion(7, 3, "通过"), "可储备专家")

    def test_conclusion_high_value_trace(self):
        """Trace 资产 >= 9 → 高价值trace"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(4, 10, "通过"), "高价值trace")
        self.assertEqual(determine_conclusion(3, 9, "通过"), "高价值trace")

    def test_conclusion_both(self):
        """两者同时满足 → 可储备专家 + 高价值trace"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(8, 10, "通过"), "可储备专家 + 高价值trace")

    def test_conclusion_manual_review(self):
        """专家能力 >= 5 或 Trace >= 6 → 待人工复核"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(5, 3, "通过"), "待人工复核")
        self.assertEqual(determine_conclusion(3, 6, "通过"), "待人工复核")
        self.assertEqual(determine_conclusion(6, 8, "通过"), "待人工复核")

    def test_conclusion_reject(self):
        """都不满足 → 拒绝"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(4, 5, "通过"), "拒绝")
        self.assertEqual(determine_conclusion(0, 0, "通过"), "拒绝")

    def test_conclusion_pre_screen_reject(self):
        """粗筛拒绝 → 最终拒绝，不论分数"""
        from projects.expert_review.writeback import determine_conclusion
        self.assertEqual(determine_conclusion(10, 12, "拒绝"), "拒绝")


# ============================================================
# Part 4: ai_review 输入组装测试
# ============================================================

class TestAIReviewInputBuild(unittest.TestCase):
    """测试 ai_review.py 的 _build_input_text"""

    def test_build_input_text(self):
        from projects.expert_review.ai_review import _build_input_text
        config = {
            "field_mapping": {
                "task_description": "任务描述",
                "expert_name": "专家姓名",
                "expert_id": "专家ID",
                "position": "岗位方向",
                "final_product": "最终产物",
            }
        }
        fields = {
            "任务描述": "实现一个分布式缓存系统",
            "专家姓名": "张三",
            "专家ID": "12345",
            "岗位方向": "Coding",
            "最终产物": {"link": "https://github.com/example"},
        }
        trace_content = '{"type":"human","content":"你好"}'

        text = _build_input_text(fields, trace_content, config)

        self.assertIn("张三", text)
        self.assertIn("12345", text)
        self.assertIn("Coding", text)
        self.assertIn("分布式缓存系统", text)
        self.assertIn("https://github.com/example", text)
        self.assertIn("Trace 日志", text)
        self.assertIn("你好", text)

    def test_build_input_text_no_product_link(self):
        from projects.expert_review.ai_review import _build_input_text
        config = {
            "field_mapping": {
                "task_description": "任务描述",
                "expert_name": "专家姓名",
                "expert_id": "专家ID",
                "position": "岗位方向",
                "final_product": "最终产物",
            }
        }
        fields = {"任务描述": "test", "专家姓名": "李四"}

        text = _build_input_text(fields, "trace...", config)
        self.assertNotIn("最终产物链接", text)  # 没有链接则不输出该节


if __name__ == "__main__":
    unittest.main()
