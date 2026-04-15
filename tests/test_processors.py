#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Processor 框架 & ctx_utils 单元测试

覆盖:
- ProcessorContext 创建
- Processor 注册表 (register/get_processor)
- feishu_fetch: mock Feishu API → ctx.data 填充 + ctx_data.json 写出
- feishu_writeback: 字段映射 + status_mapping + ctx_data.json 读入
- ctx_utils: load_ctx_data / save_ctx_data
- pipeline_runner: processor + script 模式、ctx_data 传递
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# Part 1: ProcessorContext 和注册表
# ============================================================

class TestProcessorFramework(unittest.TestCase):
    """测试 Processor 框架基础设施"""

    def test_processor_context_creation(self):
        from core.processors import ProcessorContext
        ctx = ProcessorContext(
            record_id="rec_123",
            project_dir="/tmp/test",
            config={"project": {"name": "test"}},
            app_token="token",
            table_id="table",
        )
        self.assertEqual(ctx.record_id, "rec_123")
        self.assertEqual(ctx.app_token, "token")
        self.assertIsInstance(ctx.data, dict)
        self.assertEqual(len(ctx.data), 0)

    def test_processor_context_from_config(self):
        from core.processors import ProcessorContext
        config = {
            "feishu": {
                "app_id": "test_id",
                "app_secret": "test_secret",
                "app_token": "test_token",
                "table_id": "test_table",
            },
            "workspace": {"base_dir": tempfile.mkdtemp()},
        }
        ctx = ProcessorContext.from_config(config, "rec_456", "/tmp/proj")
        self.assertEqual(ctx.record_id, "rec_456")
        self.assertEqual(ctx.app_token, "test_token")
        self.assertEqual(ctx.table_id, "test_table")
        self.assertIsNotNone(ctx.client)
        self.assertEqual(ctx.client.app_id, "test_id")

    def test_processor_context_shared_data(self):
        """验证多个 processor 共享同一个 ctx.data"""
        from core.processors import ProcessorContext
        ctx = ProcessorContext()
        ctx.data["key1"] = "value1"
        self.assertEqual(ctx.data["key1"], "value1")
        ctx.data["key2"] = {"nested": True}
        self.assertEqual(ctx.data["key2"]["nested"], True)

    def test_register_and_get_processor(self):
        from core.processors import get_processor
        # 只有 feishu_fetch 和 feishu_writeback 是通用 processor
        for name in ("feishu_fetch", "feishu_writeback"):
            cls = get_processor(name)
            self.assertIsNotNone(cls, f"{name} 未注册")

    def test_get_unknown_processor_raises(self):
        from core.processors import get_processor
        with self.assertRaises(KeyError):
            get_processor("nonexistent_processor")

    def test_base_processor_run_not_implemented(self):
        from core.processors import BaseProcessor, ProcessorContext
        bp = BaseProcessor({"name": "test"})
        ctx = ProcessorContext()
        with self.assertRaises(NotImplementedError):
            bp.run(ctx)


# ============================================================
# Part 2: ctx_utils
# ============================================================

class TestCtxUtils(unittest.TestCase):

    def test_save_and_load(self):
        from core.ctx_utils import load_ctx_data, save_ctx_data
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            data = {"key": "value", "nested": {"a": 1}, "list": [1, 2, 3]}
            save_ctx_data(path, data)
            loaded = load_ctx_data(path)
            self.assertEqual(loaded, data)
        finally:
            os.unlink(path)

    def test_save_chinese(self):
        from core.ctx_utils import load_ctx_data, save_ctx_data
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            data = {"name": "张三", "desc": "测试中文"}
            save_ctx_data(path, data)
            loaded = load_ctx_data(path)
            self.assertEqual(loaded["name"], "张三")
            # 验证文件内容是 ensure_ascii=False
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("张三", content)
        finally:
            os.unlink(path)

    def test_load_nonexistent_raises(self):
        from core.ctx_utils import load_ctx_data
        with self.assertRaises(FileNotFoundError):
            load_ctx_data("/nonexistent/ctx_data.json")


# ============================================================
# Part 3: feishu_fetch
# ============================================================

class TestFeishuFetchProcessor(unittest.TestCase):

    def _make_ctx(self, config=None):
        from core.processors import ProcessorContext
        if config is None:
            config = {
                "data_source": {
                    "type": "feishu_record",
                    "fields": {
                        "title": "标题",
                        "content": "内容",
                        "author": "作者",
                    },
                },
            }
        mock_client = MagicMock()
        workspace = tempfile.mkdtemp()
        return ProcessorContext(
            record_id="rec_001",
            project_dir="/tmp/test",
            config=config,
            client=mock_client,
            app_token="tok",
            table_id="tbl",
            workspace_dir=workspace,
        ), mock_client

    def test_fetch_fills_ctx_data(self):
        from core.processors.feishu_fetch import FeishuFetchProcessor

        ctx, mock_client = self._make_ctx()
        mock_client.get_record.return_value = {
            "fields": {
                "标题": "测试标题",
                "内容": {"text": "测试内容正文"},
                "作者": [{"name": "张三"}],
            }
        }

        proc = FeishuFetchProcessor({"name": "fetch"})
        exit_code = proc.run(ctx)

        self.assertEqual(exit_code, 0)
        self.assertEqual(ctx.data["title"], "测试标题")
        self.assertEqual(ctx.data["content"], "测试内容正文")
        self.assertEqual(ctx.data["author"], "张三")
        self.assertIn("_raw_title", ctx.data)
        self.assertIn("_raw_fields", ctx.data)

    def test_fetch_writes_ctx_data_json(self):
        """feishu_fetch 应写出 ctx_data.json"""
        from core.processors.feishu_fetch import FeishuFetchProcessor

        ctx, mock_client = self._make_ctx()
        mock_client.get_record.return_value = {
            "fields": {"标题": "Test"}
        }

        proc = FeishuFetchProcessor({"name": "fetch"})
        proc.run(ctx)

        ctx_data_path = os.path.join(ctx.workspace_dir, "ctx_data.json")
        self.assertTrue(os.path.isfile(ctx_data_path))
        with open(ctx_data_path) as f:
            data = json.load(f)
        self.assertEqual(data["title"], "Test")

    def test_fetch_fallback_to_field_mapping(self):
        from core.processors.feishu_fetch import FeishuFetchProcessor

        config = {
            "field_mapping": {
                "name": "姓名",
                "age": "年龄",
            },
        }
        ctx, mock_client = self._make_ctx(config)
        mock_client.get_record.return_value = {
            "fields": {
                "姓名": "李四",
                "年龄": 30,
            }
        }

        proc = FeishuFetchProcessor({"name": "fetch"})
        exit_code = proc.run(ctx)

        self.assertEqual(exit_code, 0)
        self.assertEqual(ctx.data["name"], "李四")
        self.assertEqual(ctx.data["age"], "30")

    def test_fetch_no_mapping_returns_0(self):
        from core.processors.feishu_fetch import FeishuFetchProcessor
        ctx, _ = self._make_ctx({})
        proc = FeishuFetchProcessor({"name": "fetch"})
        self.assertEqual(proc.run(ctx), 0)


# ============================================================
# Part 4: feishu_writeback
# ============================================================

class TestFeishuWritebackProcessor(unittest.TestCase):

    def _make_ctx(self, data=None, config=None):
        from core.processors import ProcessorContext
        if config is None:
            config = {
                "data_sink": {
                    "type": "feishu_record",
                    "field_mapping": {
                        "review_status": "审核状态",
                        "review_note": "机审说明",
                    },
                    "status_mapping": {
                        "pass": "审核通过",
                        "reject": "已拒绝",
                    },
                },
            }
        mock_client = MagicMock()
        workspace = tempfile.mkdtemp()
        ctx = ProcessorContext(
            record_id="rec_001",
            project_dir="/tmp/test",
            config=config,
            client=mock_client,
            app_token="tok",
            table_id="tbl",
            data=data or {},
            workspace_dir=workspace,
        )
        return ctx, mock_client

    def test_writeback_maps_fields(self):
        from core.processors.feishu_writeback import FeishuWritebackProcessor

        data = {
            "review_status": "pass",
            "review_note": "审核通过，内容质量良好",
        }
        ctx, mock_client = self._make_ctx(data)
        mock_client.update_record.return_value = {"code": 0}

        proc = FeishuWritebackProcessor({"name": "writeback"})
        exit_code = proc.run(ctx)

        self.assertEqual(exit_code, 0)
        mock_client.update_record.assert_called_once()
        call_args = mock_client.update_record.call_args
        fields = call_args[0][3]
        self.assertEqual(fields["审核状态"], "审核通过")
        self.assertEqual(fields["机审说明"], "审核通过，内容质量良好")

    def test_writeback_reads_ctx_data_json(self):
        """feishu_writeback 应从 ctx_data.json 读取最新数据"""
        from core.processors.feishu_writeback import FeishuWritebackProcessor

        ctx, mock_client = self._make_ctx({})
        mock_client.update_record.return_value = {"code": 0}

        # 写入 ctx_data.json（模拟业务脚本写入）
        ctx_data_path = os.path.join(ctx.workspace_dir, "ctx_data.json")
        with open(ctx_data_path, "w") as f:
            json.dump({"review_status": "reject", "review_note": "不合规"}, f)

        proc = FeishuWritebackProcessor({"name": "writeback"})
        exit_code = proc.run(ctx)

        self.assertEqual(exit_code, 0)
        fields = mock_client.update_record.call_args[0][3]
        self.assertEqual(fields["审核状态"], "已拒绝")

    def test_writeback_status_mapping(self):
        from core.processors.feishu_writeback import FeishuWritebackProcessor

        data = {"review_status": "reject"}
        ctx, mock_client = self._make_ctx(data)
        mock_client.update_record.return_value = {"code": 0}

        proc = FeishuWritebackProcessor({"name": "writeback"})
        proc.run(ctx)

        fields = mock_client.update_record.call_args[0][3]
        self.assertEqual(fields["审核状态"], "已拒绝")

    def test_writeback_no_matching_keys(self):
        from core.processors.feishu_writeback import FeishuWritebackProcessor

        ctx, mock_client = self._make_ctx({"unrelated_key": "value"})
        proc = FeishuWritebackProcessor({"name": "writeback"})
        exit_code = proc.run(ctx)

        self.assertEqual(exit_code, 0)
        mock_client.update_record.assert_not_called()

    def test_writeback_api_failure_returns_1(self):
        from core.processors.feishu_writeback import FeishuWritebackProcessor

        data = {"review_status": "pass"}
        ctx, mock_client = self._make_ctx(data)
        mock_client.update_record.side_effect = RuntimeError("API error")

        proc = FeishuWritebackProcessor({"name": "writeback"})
        exit_code = proc.run(ctx)
        self.assertEqual(exit_code, 1)


# ============================================================
# Part 5: writeback.py 业务脚本的算分逻辑
# ============================================================

class TestWritebackScoreCompute(unittest.TestCase):

    def test_extract_scores_basic(self):

        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 2, "evidence": "..."},
                "iteration_quality": {"score": 3, "evidence": "..."},
                "professional_judgment": {"score": 3, "evidence": "..."},
                "total": 8,
            }
        }
        dims = [
            {"key": "task_complexity", "max_score": 3},
            {"key": "iteration_quality", "max_score": 3},
            {"key": "professional_judgment", "max_score": 4},
        ]
        scores = extract_scores(ai_result, "expert_ability", dims)
        self.assertEqual(scores["task_complexity"], 2)
        self.assertEqual(scores["total"], 8)

    def test_extract_scores_clamped(self):
        from projects.expert_review.writeback import extract_scores
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 10},
                "iteration_quality": {"score": -1},
                "professional_judgment": {"score": 4},
            }
        }
        dims = [
            {"key": "task_complexity", "max_score": 3},
            {"key": "iteration_quality", "max_score": 3},
            {"key": "professional_judgment", "max_score": 4},
        ]
        scores = extract_scores(ai_result, "expert_ability", dims)
        self.assertEqual(scores["task_complexity"], 3)
        self.assertEqual(scores["iteration_quality"], 0)

    def test_compute_composite_score(self):
        from projects.expert_review.writeback import compute_composite_score
        score = compute_composite_score(8, 10, 10, 12)
        self.assertAlmostEqual(score, 81.7, places=1)

    def test_determine_conclusion_pass(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(8, 10, "通过", pass_score=70)
        self.assertEqual(conclusion, "通过")
        self.assertGreaterEqual(score, 70)

    def test_determine_conclusion_fail(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(3, 4, "通过", pass_score=70)
        self.assertEqual(conclusion, "不通过")
        self.assertLess(score, 70)

    def test_determine_conclusion_pre_screen_reject(self):
        from projects.expert_review.writeback import determine_conclusion
        conclusion, score = determine_conclusion(10, 12, "拒绝")
        self.assertEqual(conclusion, "不通过")
        self.assertEqual(score, 0.0)


# ============================================================
# Part 6: pre_screen.py 检查函数
# ============================================================

class TestPreScreenChecks(unittest.TestCase):

    def test_task_authenticity_pass(self):

        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("实现一个分布式缓存系统，支持多节点同步")
        self.assertTrue(result["passed"])

    def test_task_authenticity_empty(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "reject")

    def test_task_authenticity_demo(self):
        from projects.expert_review.pre_screen import check_task_authenticity
        result = check_task_authenticity("hello world")
        self.assertFalse(result["passed"])

    def test_tool_loop_exists_pass(self):
        from core.trace_parser import TraceAnalysis
        from projects.expert_review.pre_screen import check_tool_loop_exists
        trace = TraceAnalysis(
            is_valid=True, has_tool_calls=True, tool_call_count=5,
            conversation_rounds=3, total_lines=10,
        )
        result = check_tool_loop_exists(trace)
        self.assertTrue(result["passed"])

    def test_tool_loop_exists_fail(self):
        from core.trace_parser import TraceAnalysis
        from projects.expert_review.pre_screen import check_tool_loop_exists
        trace = TraceAnalysis(
            is_valid=True, has_tool_calls=False, tool_call_count=0,
            conversation_rounds=3, total_lines=10,
        )
        result = check_tool_loop_exists(trace)
        self.assertFalse(result["passed"])

    def test_verification_exists_with_bash(self):
        from projects.expert_review.pre_screen import check_verification_exists
        result = check_verification_exists("  [工具调用] Bash: pytest tests/")
        self.assertTrue(result["passed"])

    def test_verification_exists_no_bash(self):
        from projects.expert_review.pre_screen import check_verification_exists
        result = check_verification_exists("  [工具调用] Read: /tmp/file.py")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_compliance_pass(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("正常的代码内容，没有密钥")
        self.assertTrue(result["passed"])

    def test_compliance_secret_detected(self):
        from projects.expert_review.pre_screen import check_compliance
        result = check_compliance("using key sk-abcdefghijklmnopqrstuvwxyz1234567890")
        self.assertFalse(result["passed"])
        self.assertEqual(result["action"], "manual_review")

    def test_final_product_exists_link(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists({"link": "https://example.com"})
        self.assertTrue(result["passed"])

    def test_final_product_exists_missing(self):
        from projects.expert_review.pre_screen import check_final_product_exists
        result = check_final_product_exists(None)
        self.assertFalse(result["passed"])


# ============================================================
# Part 7: pipeline_runner processor + script 模式
# ============================================================

class TestPipelineRunnerProcessorMode(unittest.TestCase):

    def _make_project_dir(self, config_dict):
        import yaml
        tmpdir = tempfile.mkdtemp()
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config_dict, f, allow_unicode=True)
        return tmpdir

    def test_processor_stage_execution(self):
        from core.pipeline_runner import run_pipeline

        config = {
            "project": {"name": "test_processor_pipeline"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "data_source": {
                "fields": {"title": "标题"},
            },
            "stages": [
                {
                    "name": "fetch",
                    "processor": "feishu_fetch",
                    "exit_code_handling": {0: "continue"},
                },
            ],
            "workspace": {"base_dir": tempfile.mkdtemp()},
        }
        tmpdir = self._make_project_dir(config)

        mock_client = MagicMock()
        mock_client.get_record.return_value = {
            "fields": {"标题": "Test Title"}
        }
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = mock_client
            result = run_pipeline(tmpdir, "test_record")
            self.assertEqual(result, 0)

    def test_script_stage_with_ctx_data(self):
        """验证 script 模式通过 ctx_data.json 传递数据"""
        from core.pipeline_runner import run_pipeline

        tmpdir = tempfile.mkdtemp()

        # 创建一个脚本：从 ctx_data 读，添加一个字段，写回
        script_code = """
import sys, argparse, json
p = argparse.ArgumentParser()
p.add_argument('--record-id')
p.add_argument('--project-dir')
p.add_argument('--ctx-data-file')
args = p.parse_args()

with open(args.ctx_data_file, 'r') as f:
    data = json.load(f)

data['script_ran'] = True

with open(args.ctx_data_file, 'w') as f:
    json.dump(data, f)

sys.exit(0)
"""
        with open(os.path.join(tmpdir, "test_script.py"), "w") as f:
            f.write(script_code)

        import yaml
        workspace_dir = tempfile.mkdtemp()
        config = {
            "project": {"name": "ctx_data_test"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "data_source": {"fields": {"title": "标题"}},
            "stages": [
                {
                    "name": "fetch",
                    "processor": "feishu_fetch",
                    "exit_code_handling": {0: "continue"},
                },
                {
                    "name": "custom",
                    "script": "test_script.py",
                    "exit_code_handling": {0: "continue"},
                },
            ],
            "workspace": {"base_dir": workspace_dir},
        }
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config, f, allow_unicode=True)

        mock_client = MagicMock()
        mock_client.get_record.return_value = {"fields": {"标题": "Test"}}
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = mock_client
            result = run_pipeline(tmpdir, "test_record")
            self.assertEqual(result, 0)

    def test_mixed_processor_and_script(self):
        from core.pipeline_runner import run_pipeline

        tmpdir = tempfile.mkdtemp()

        script_code = (
            "import sys, argparse\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--record-id')\n"
            "p.add_argument('--project-dir')\n"
            "p.add_argument('--ctx-data-file')\n"
            "p.parse_args()\n"
            "sys.exit(0)\n"
        )
        with open(os.path.join(tmpdir, "simple.py"), "w") as f:
            f.write(script_code)

        import yaml
        config = {
            "project": {"name": "mixed_test"},
            "feishu": {
                "app_id": "test", "app_secret": "test",
                "app_token": "test", "table_id": "test",
            },
            "data_source": {"fields": {"title": "标题"}},
            "stages": [
                {
                    "name": "fetch",
                    "processor": "feishu_fetch",
                    "exit_code_handling": {0: "continue"},
                },
                {
                    "name": "custom",
                    "script": "simple.py",
                    "exit_code_handling": {0: "continue"},
                },
            ],
            "workspace": {"base_dir": tempfile.mkdtemp()},
        }
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config, f, allow_unicode=True)

        mock_client = MagicMock()
        mock_client.get_record.return_value = {"fields": {"标题": "Test"}}
        with patch("core.processors.FeishuClient") as MockClient:
            MockClient.from_config.return_value = mock_client
            result = run_pipeline(tmpdir, "test_record")
            self.assertEqual(result, 0)

    def test_processor_stop_action(self):
        from core.pipeline_runner import _resolve_action
        action = _resolve_action({0: "continue", 1: "stop"}, 1)
        self.assertEqual(action, "stop")

    def test_processor_error_action(self):
        from core.pipeline_runner import _resolve_action
        action = _resolve_action({0: "continue"}, 3)
        self.assertEqual(action, "error")


# ============================================================
# Part 8: config_loader 新格式支持
# ============================================================

class TestConfigLoaderNewFormat(unittest.TestCase):

    def _make_config(self, config_dict):
        import yaml
        tmpdir = tempfile.mkdtemp()
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config_dict, f, allow_unicode=True)
        return tmpdir

    def test_get_field_name_data_source(self):
        from core.config_loader import get_field_name
        config = {
            "data_source": {"fields": {"title": "标题_NEW"}},
            "field_mapping": {"title": "标题_OLD"},
        }
        self.assertEqual(get_field_name(config, "title"), "标题_NEW")

    def test_get_field_name_fallback(self):
        from core.config_loader import get_field_name
        config = {"field_mapping": {"title": "标题"}}
        self.assertEqual(get_field_name(config, "title"), "标题")

    def test_get_field_name_not_found(self):
        from core.config_loader import get_field_name
        with self.assertRaises(KeyError):
            get_field_name({}, "nonexistent")

    def test_get_sink_field_name(self):
        from core.config_loader import get_sink_field_name
        config = {
            "data_sink": {
                "field_mapping": {"review_status": "审核状态"},
            },
        }
        self.assertEqual(get_sink_field_name(config, "review_status"), "审核状态")

    def test_get_sink_field_name_fallback(self):
        from core.config_loader import get_sink_field_name
        config = {"field_mapping": {"review_status": "审核状态"}}
        self.assertEqual(get_sink_field_name(config, "review_status"), "审核状态")


# ============================================================
# Part 9: content_review 示例项目
# ============================================================

class TestContentReviewProject(unittest.TestCase):

    def test_config_loadable(self):
        from core.config_loader import load_project_config

        keys = {"FEISHU_APP_ID": "test", "FEISHU_APP_SECRET": "test",
                "BITABLE_APP_TOKEN": "test", "BITABLE_TABLE_ID": "test"}
        old = {}
        for k, v in keys.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            project_dir = os.path.join(
                os.path.dirname(__file__), "..", "projects", "content_review"
            )
            config = load_project_config(project_dir)
            self.assertEqual(config["project"]["name"], "content_review")
            self.assertIn("data_source", config)
            self.assertIn("data_sink", config)
            self.assertEqual(len(config["stages"]), 3)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_prompt_and_schema_exist(self):
        project_dir = os.path.join(
            os.path.dirname(__file__), "..", "projects", "content_review"
        )
        self.assertTrue(os.path.isfile(os.path.join(project_dir, "prompt.md")))
        self.assertTrue(os.path.isfile(os.path.join(project_dir, "schema.json")))

    def test_schema_valid_json(self):
        project_dir = os.path.join(
            os.path.dirname(__file__), "..", "projects", "content_review"
        )
        with open(os.path.join(project_dir, "schema.json")) as f:
            schema = json.load(f)
        self.assertIn("name", schema)
        self.assertIn("schema", schema)


# ============================================================
# Part 10: expert_review 新 config 兼容性
# ============================================================

class TestExpertReviewNewConfig(unittest.TestCase):

    def _load_config(self):
        from core.config_loader import load_project_config

        keys = {"FEISHU_APP_ID": "test", "FEISHU_APP_SECRET": "test",
                "BITABLE_APP_TOKEN": "test", "BITABLE_TABLE_ID": "test"}
        old = {}
        for k, v in keys.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            project_dir = os.path.join(
                os.path.dirname(__file__), "..", "projects", "expert_review"
            )
            return load_project_config(project_dir)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_has_data_source_and_sink(self):
        config = self._load_config()
        self.assertIn("data_source", config)
        self.assertIn("data_sink", config)
        self.assertEqual(config["data_source"]["type"], "feishu_record")
        self.assertEqual(config["data_sink"]["type"], "feishu_record")

    def test_stages_structure(self):
        """验证 stages: 2 个 processor + 3 个 script"""
        config = self._load_config()
        processor_stages = [s for s in config["stages"] if s.get("processor")]
        script_stages = [s for s in config["stages"] if s.get("script")]
        self.assertEqual(len(processor_stages), 2)  # feishu_fetch, feishu_writeback
        self.assertEqual(len(script_stages), 3)  # pre_screen, ai_review, writeback

    def test_backward_compatible_field_mapping(self):
        config = self._load_config()
        self.assertIn("field_mapping", config)
        self.assertIn("task_description", config["field_mapping"])

    def test_scoring_config_intact(self):
        config = self._load_config()
        scoring = config["scoring"]
        self.assertEqual(scoring["pass_score"], 70)
        expert_dims = scoring["expert_ability"]["dimensions"]
        self.assertEqual(sum(d["max_score"] for d in expert_dims), 10)
        trace_dims = scoring["trace_asset"]["dimensions"]
        self.assertEqual(sum(d["max_score"] for d in trace_dims), 12)


if __name__ == "__main__":
    unittest.main()
