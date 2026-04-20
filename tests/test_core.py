#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
core/ 基础层单元测试

覆盖: config_loader, feishu_utils, trace_parser, daytona_runner 数据类
"""

import json
import os
import sys
import tempfile
import unittest

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestConfigLoader(unittest.TestCase):
    """测试 core/config_loader.py"""

    def _make_config_dir(self, config_dict):
        """创建临时目录 + config.yaml"""
        tmpdir = tempfile.mkdtemp()
        import yaml
        with open(os.path.join(tmpdir, "config.yaml"), "w") as f:
            yaml.dump(config_dict, f, allow_unicode=True)
        return tmpdir

    def test_load_config_with_env_vars(self):
        """config 中飞书留空，应回退到环境变量"""
        from core.config_loader import load_project_config, get_field_name

        config_dict = {
            "project": {"name": "test_project"},
            "feishu": {},
            "field_mapping": {"trace_file": "Trace文件", "name": "姓名"},
            "stages": [],
        }
        tmpdir = self._make_config_dir(config_dict)

        env = {
            "FEISHU_APP_ID": "id_from_env",
            "FEISHU_APP_SECRET": "secret_from_env",
            "BITABLE_APP_TOKEN": "token_from_env",
            "BITABLE_TABLE_ID": "table_from_env",
        }
        old_env = {}
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            config = load_project_config(tmpdir)
            self.assertEqual(config["feishu"]["app_id"], "id_from_env")
            self.assertEqual(config["feishu"]["app_secret"], "secret_from_env")
            self.assertEqual(config["feishu"]["app_token"], "token_from_env")
            self.assertEqual(config["feishu"]["table_id"], "table_from_env")

            # 测试字段映射
            self.assertEqual(get_field_name(config, "trace_file"), "Trace文件")
            self.assertEqual(get_field_name(config, "name"), "姓名")

            # 不存在的映射应报错
            with self.assertRaises(KeyError):
                get_field_name(config, "nonexistent_field")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_load_config_values_override_env(self):
        """config.yaml 中显式写了值，应优先于环境变量"""
        from core.config_loader import load_project_config

        config_dict = {
            "project": {"name": "override_test"},
            "feishu": {
                "app_id": "id_from_yaml",
                "app_secret": "secret_from_yaml",
                "app_token": "token_from_yaml",
                "table_id": "table_from_yaml",
            },
            "stages": [],
        }
        tmpdir = self._make_config_dir(config_dict)

        env = {
            "FEISHU_APP_ID": "id_from_env",
            "FEISHU_APP_SECRET": "secret_from_env",
            "BITABLE_APP_TOKEN": "token_from_env",
            "BITABLE_TABLE_ID": "table_from_env",
        }
        old_env = {}
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            config = load_project_config(tmpdir)
            # YAML 值应覆盖环境变量
            self.assertEqual(config["feishu"]["app_id"], "id_from_yaml")
            self.assertEqual(config["feishu"]["app_secret"], "secret_from_yaml")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_missing_config_file_exits(self):
        """config.yaml 不存在应 sys.exit"""
        from core.config_loader import load_project_config
        with self.assertRaises(SystemExit):
            load_project_config("/nonexistent/path")

    def test_missing_feishu_config_exits(self):
        """飞书配置全缺应 sys.exit"""
        from core.config_loader import load_project_config

        config_dict = {"project": {"name": "empty_feishu"}, "feishu": {}, "stages": []}
        tmpdir = self._make_config_dir(config_dict)

        # 确保环境变量也没设
        keys = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "BITABLE_APP_TOKEN",
                "BITABLE_TABLE_ID", "APP_ID", "APP_SECRET", "APP_TOKEN", "COMMIT_TABLE_ID"]
        old_env = {}
        for k in keys:
            old_env[k] = os.environ.pop(k, None)

        try:
            with self.assertRaises(SystemExit):
                load_project_config(tmpdir)
        finally:
            for k, v in old_env.items():
                if v is not None:
                    os.environ[k] = v


class TestFeishuUtils(unittest.TestCase):
    """测试 core/feishu_utils.py 的无状态工具函数"""

    def test_normalize_field_value_none(self):
        from core.feishu_utils import normalize_field_value
        self.assertEqual(normalize_field_value(None), "")

    def test_normalize_field_value_string(self):
        from core.feishu_utils import normalize_field_value
        self.assertEqual(normalize_field_value("hello"), "hello")

    def test_normalize_field_value_dict_with_text(self):
        from core.feishu_utils import normalize_field_value
        self.assertEqual(normalize_field_value({"text": "world"}), "world")

    def test_normalize_field_value_list(self):
        from core.feishu_utils import normalize_field_value
        result = normalize_field_value([{"text": "a"}, {"name": "b"}, "c"])
        self.assertEqual(result, "a, b, c")

    def test_normalize_field_value_number(self):
        from core.feishu_utils import normalize_field_value
        self.assertEqual(normalize_field_value(42), "42")
        self.assertEqual(normalize_field_value(3.14), "3.14")

    def test_extract_attachment_file_token(self):
        from core.feishu_utils import extract_attachment_file_token
        self.assertEqual(
            extract_attachment_file_token([{"file_token": "abc123", "name": "trace.jsonl"}]),
            "abc123",
        )
        self.assertIsNone(extract_attachment_file_token([]))
        self.assertIsNone(extract_attachment_file_token(None))
        self.assertIsNone(extract_attachment_file_token("string"))

    def test_extract_attachment_entries_and_tokens(self):
        from core.feishu_utils import extract_attachment_entries, extract_attachment_file_tokens
        field_value = [
            {"file_token": "abc123", "name": "trace1.jsonl"},
            {"name": "invalid"},
            {"file_token": "def456", "name": "trace2.jsonl"},
        ]
        self.assertEqual(len(extract_attachment_entries(field_value)), 3)
        self.assertEqual(extract_attachment_file_tokens(field_value), ["abc123", "def456"])

    def test_extract_link_url(self):
        from core.feishu_utils import extract_link_url
        self.assertEqual(extract_link_url({"link": "https://example.com"}), "https://example.com")
        self.assertEqual(extract_link_url("https://direct.com"), "https://direct.com")
        self.assertEqual(extract_link_url(None), "")
        self.assertEqual(extract_link_url(123), "")

    def test_feishu_client_construction(self):
        from core.feishu_utils import FeishuClient
        client = FeishuClient("id", "secret")
        self.assertEqual(client.app_id, "id")
        self.assertEqual(client.app_secret, "secret")

    def test_feishu_client_from_config(self):
        from core.feishu_utils import FeishuClient
        config = {
            "feishu": {
                "app_id": "cfg_id",
                "app_secret": "cfg_secret",
                "app_token": "cfg_token",
                "table_id": "cfg_table",
            }
        }
        client = FeishuClient.from_config(config)
        self.assertEqual(client.app_id, "cfg_id")


class TestTraceParser(unittest.TestCase):
    """测试 core/trace_parser.py"""

    def _write_trace(self, lines):
        """写入临时 trace 文件"""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        f.close()
        return f.name

    def test_parse_valid_trace(self):
        from core.trace_parser import parse_trace_file
        path = self._write_trace([
            {"type": "human", "content": "你好"},
            {"type": "assistant", "model": "claude-opus-4-20250514", "content": [
                {"type": "text", "text": "你好！"},
                {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
            ]},
            {"type": "tool_result", "content": "file1\nfile2"},
            {"type": "human", "content": "继续"},
            {"type": "assistant", "model": "claude-opus-4-20250514", "content": [
                {"type": "text", "text": "好的"}
            ]},
        ])
        try:
            result = parse_trace_file(path)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.conversation_rounds, 2)
            self.assertEqual(result.model_name, "claude-opus-4-20250514")
            self.assertTrue(result.is_sota_model)
            self.assertTrue(result.has_tool_calls)
            self.assertGreaterEqual(result.tool_call_count, 1)
            self.assertEqual(result.total_lines, 5)
        finally:
            os.unlink(path)

    def test_parse_non_opus_model(self):
        from core.trace_parser import parse_trace_file
        path = self._write_trace([
            {"type": "human", "content": "test"},
            {"type": "assistant", "model": "claude-sonnet-4-20250514", "content": []},
        ])
        try:
            result = parse_trace_file(path)
            self.assertTrue(result.is_valid)
            self.assertFalse(result.is_sota_model)
            self.assertIn("sonnet", result.model_name)
        finally:
            os.unlink(path)

    def test_parse_empty_file(self):
        from core.trace_parser import parse_trace_file
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        f.close()
        try:
            result = parse_trace_file(f.name)
            self.assertFalse(result.is_valid)
        finally:
            os.unlink(f.name)

    def test_parse_nonexistent_file(self):
        from core.trace_parser import parse_trace_file
        result = parse_trace_file("/nonexistent/trace.jsonl")
        self.assertFalse(result.is_valid)
        self.assertTrue(len(result.errors) > 0)

    def test_truncate_trace_content(self):
        from core.trace_parser import truncate_trace_content
        lines = [{"type": "human", "content": f"msg {i}"} for i in range(100)]
        path = self._write_trace(lines)
        try:
            content = truncate_trace_content(path, max_rounds=5, max_bytes=999999)
            self.assertIn("已截断", content)
        finally:
            os.unlink(path)

    def test_truncate_trace_bytes_limit(self):
        from core.trace_parser import truncate_trace_content
        lines = [{"type": "assistant", "content": "x" * 500} for _ in range(20)]
        path = self._write_trace(lines)
        try:
            content = truncate_trace_content(path, max_rounds=999, max_bytes=1000)
            self.assertIn("已截断", content)
        finally:
            os.unlink(path)


    def test_parse_new_recordType_format(self):
        """新版 Claude Code session export 格式 (recordType=message)"""
        from core.trace_parser import parse_trace_file
        path = self._write_trace([
            {"recordType": "session", "sessionId": "abc", "metrics": {"messageCount": 5}},
            {"recordType": "message", "message": {
                "type": "user", "text": "你好", "isMeta": False,
            }},
            {"recordType": "message", "message": {
                "type": "assistant", "model": "claude-opus-4-6",
                "text": "好的，我来帮你",
                "toolCalls": [
                    {"id": "tc1", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
                    {"id": "tc2", "name": "Bash", "input": {"command": "ls"}},
                ],
            }},
            {"recordType": "message", "message": {
                "type": "user", "text": "[tool_result] file content",
                "toolResults": [{"toolUseId": "tc1", "content": "file content"}],
            }},
            {"recordType": "message", "message": {
                "type": "assistant", "model": "claude-opus-4-6",
                "text": "完成了",
            }},
            {"recordType": "message", "message": {
                "type": "user", "text": "谢谢", "isMeta": False,
            }},
        ])
        try:
            result = parse_trace_file(path)
            self.assertTrue(result.is_valid)
            # 只有2条真正的用户消息（排除 toolResults 的那条）
            self.assertEqual(result.conversation_rounds, 2)
            self.assertEqual(result.model_name, "claude-opus-4-6")
            self.assertTrue(result.is_sota_model)
            self.assertTrue(result.has_tool_calls)
            self.assertEqual(result.tool_call_count, 2)  # 2 toolCalls
            self.assertEqual(result.total_lines, 6)
        finally:
            os.unlink(path)

    def test_parse_new_format_non_opus(self):
        """新格式下非 opus 模型的检测"""
        from core.trace_parser import parse_trace_file
        path = self._write_trace([
            {"recordType": "session", "sessionId": "xyz"},
            {"recordType": "message", "message": {
                "type": "user", "text": "test",
            }},
            {"recordType": "message", "message": {
                "type": "assistant", "model": "claude-sonnet-4-6",
                "text": "ok",
            }},
        ])
        try:
            result = parse_trace_file(path)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.conversation_rounds, 1)
            self.assertFalse(result.is_sota_model)
            self.assertIn("sonnet", result.model_name)
        finally:
            os.unlink(path)

    def test_extractor_new_format(self):
        """新格式下精简提取器能正确输出"""
        from core.trace_extractor import extract_user_focused_content
        path = self._write_trace([
            {"recordType": "session", "sessionId": "abc"},
            {"recordType": "message", "message": {
                "type": "user", "text": "帮我写一个函数",
            }},
            {"recordType": "message", "message": {
                "type": "assistant", "model": "claude-opus-4-6",
                "text": "好的",
                "toolCalls": [
                    {"id": "tc1", "name": "Write", "input": {"file_path": "/tmp/f.py"}},
                ],
            }},
            {"recordType": "message", "message": {
                "type": "user", "text": "[tool_result] ok",
                "toolResults": [{"toolUseId": "tc1", "content": "ok"}],
            }},
            {"recordType": "message", "message": {
                "type": "assistant", "model": "claude-opus-4-6",
                "text": "搞定了",
            }},
        ])
        try:
            content = extract_user_focused_content(path)
            # 应该只有1轮用户消息，不含 tool_result 那条
            self.assertIn("提取 1 轮用户消息", content)
            self.assertIn("帮我写一个函数", content)
            self.assertIn("[工具调用] Write", content)
            self.assertIn("搞定了", content)
            # 不应包含 tool_result 作为用户消息
            self.assertNotIn("[第2轮", content)
        finally:
            os.unlink(path)


class TestTraceBundle(unittest.TestCase):
    """测试 core/trace_bundle.py"""

    def test_download_and_merge_trace_attachments(self):
        from core.trace_bundle import download_and_merge_trace_attachments

        class FakeClient:
            def __init__(self, payloads):
                self.payloads = payloads

            def download_attachment(self, file_token, output_path, download_url=None):
                with open(output_path, "wb") as f:
                    f.write(self.payloads[file_token])

        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "trace.jsonl")
        trace_field = [
            {"file_token": "a1", "name": "trace_a.jsonl"},
            {"file_token": "b2", "name": "trace_b.jsonl"},
        ]
        client = FakeClient({
            "a1": b'{"type":"human","content":"first"}\n',
            "b2": b'{"type":"human","content":"second"}',
        })

        try:
            bundle = download_and_merge_trace_attachments(client, trace_field, output_path)
            self.assertEqual(bundle.attachment_count, 2)
            self.assertEqual(bundle.attachment_names, ["trace_a.jsonl", "trace_b.jsonl"])
            self.assertTrue(os.path.isfile(output_path))
            with open(output_path, "rb") as f:
                merged = f.read()
            self.assertIn(b'{"type":"human","content":"first"}\n', merged)
            self.assertIn(b'{"type":"human","content":"second"}\n', merged)
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)
            os.rmdir(tmpdir)

class TestDaytonaRunnerDataClasses(unittest.TestCase):
    """测试 core/daytona_runner.py 的数据类和 JSON 修复函数"""

    def test_daytona_runner_import_without_sdk(self):
        import core.daytona_runner as dr
        self.assertTrue(hasattr(dr, "DaytonaRunConfig"))
        self.assertTrue(hasattr(dr, "run_claude_in_sandbox"))

    def test_daytona_run_config_defaults(self):
        from core.daytona_runner import DaytonaRunConfig
        cfg = DaytonaRunConfig()
        self.assertEqual(cfg.snapshot, "claude-code-snapshot")
        self.assertEqual(cfg.cpu, 2)
        self.assertEqual(cfg.memory, 4)
        self.assertEqual(cfg.timeout, 600)
        self.assertEqual(cfg.poll_interval, 5)

    def test_claude_run_result_defaults(self):
        from core.daytona_runner import ClaudeRunResult
        result = ClaudeRunResult()
        self.assertFalse(result.success)
        self.assertIsNone(result.result_json)
        self.assertEqual(result.raw_output, "")
        self.assertEqual(result.error, "")

    def test_try_repair_json_clean(self):
        from core.daytona_runner import _try_repair_json
        raw = '{"score": 8}'
        self.assertEqual(_try_repair_json(raw), raw)

    def test_try_repair_json_code_block(self):
        from core.daytona_runner import _try_repair_json
        raw = 'some text\n```json\n{"score": 8}\n```\nmore text'
        result = _try_repair_json(raw)
        self.assertEqual(json.loads(result), {"score": 8})

    def test_try_repair_json_extract_braces(self):
        from core.daytona_runner import _try_repair_json
        raw = 'prefix {"score": 8} suffix'
        result = _try_repair_json(raw)
        self.assertEqual(json.loads(result), {"score": 8})

    def test_try_repair_json_structured_output(self):
        from core.daytona_runner import _try_repair_json
        raw = json.dumps({"structured_output": {"score": 8, "detail": "good"}})
        result = _try_repair_json(raw)
        parsed = json.loads(result)
        self.assertEqual(parsed["score"], 8)

    def test_run_claude_missing_api_key(self):
        """没有 API key 应直接返回错误，不真正连沙箱"""
        from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox
        cfg = DaytonaRunConfig(api_key="", openrouter_api_key="test")
        result = run_claude_in_sandbox(cfg, "prompt", "schema", "input")
        self.assertFalse(result.success)
        self.assertIn("DAYTONA_API_KEY", result.error)

    def test_run_claude_missing_openrouter_key(self):
        from core.daytona_runner import DaytonaRunConfig, run_claude_in_sandbox
        cfg = DaytonaRunConfig(api_key="test", openrouter_api_key="")
        result = run_claude_in_sandbox(cfg, "prompt", "schema", "input")
        self.assertFalse(result.success)
        self.assertIn("OPENROUTER_API_KEY", result.error)


class TestExpertReviewConfig(unittest.TestCase):
    """测试 projects/expert_review/config.yaml 完整性"""

    def _load_config(self):
        """加载 expert_review 配置（需要飞书 env mock）"""
        from core.config_loader import load_project_config

        keys = {"FEISHU_APP_ID": "test", "FEISHU_APP_SECRET": "test",
                "BITABLE_APP_TOKEN": "test", "BITABLE_TABLE_ID": "test"}
        old = {}
        for k, v in keys.items():
            old[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            project_dir = os.path.join(os.path.dirname(__file__), "..", "projects", "expert_review")
            return load_project_config(project_dir)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_config_has_required_sections(self):
        config = self._load_config()
        self.assertIn("project", config)
        self.assertIn("feishu", config)
        self.assertIn("stages", config)
        self.assertIn("ai_review", config)
        self.assertIn("scoring", config)
        self.assertIn("field_mapping", config)
        self.assertIn("pre_screen", config)
        self.assertIn("workspace", config)

    def test_stages_have_required_fields(self):
        config = self._load_config()
        for stage in config["stages"]:
            self.assertIn("name", stage, f"stage 缺少 name: {stage}")
            self.assertIn("script", stage, f"stage 缺少 script: {stage}")
            # exit_code_handling 可选，pipeline_runner 有默认策略

    def test_stages_scripts_exist(self):
        """所有 stage 引用的脚本文件必须存在"""
        config = self._load_config()
        project_dir = os.path.join(os.path.dirname(__file__), "..", "projects", "expert_review")
        for stage in config["stages"]:
            script_path = os.path.join(project_dir, stage["script"])
            self.assertTrue(
                os.path.isfile(script_path),
                f"阶段 {stage['name']} 的脚本不存在: {script_path}",
            )

    def test_scoring_dimensions_complete(self):
        config = self._load_config()
        # 专家能力分
        expert_dims = config["scoring"]["expert_ability"]["dimensions"]
        expert_keys = [d["key"] for d in expert_dims]
        self.assertIn("task_complexity", expert_keys)
        self.assertIn("iteration_quality", expert_keys)
        self.assertIn("professional_judgment", expert_keys)
        expert_max = sum(d["max_score"] for d in expert_dims)
        self.assertEqual(expert_max, 10)

        # Trace 资产分
        trace_dims = config["scoring"]["trace_asset"]["dimensions"]
        trace_keys = [d["key"] for d in trace_dims]
        self.assertIn("authenticity", trace_keys)
        self.assertIn("info_density", trace_keys)
        self.assertIn("tool_loop", trace_keys)
        self.assertIn("correction_value", trace_keys)
        self.assertIn("verification_loop", trace_keys)
        self.assertIn("compliance", trace_keys)
        trace_max = sum(d["max_score"] for d in trace_dims)
        self.assertEqual(trace_max, 12)

    def test_field_mapping_completeness(self):
        """field_mapping 应覆盖所有业务需要的逻辑字段"""
        config = self._load_config()
        fm = config["field_mapping"]
        required_logical_names = [
            # 输入字段
            "trace_file", "task_description", "expert_name", "expert_id",
            "position", "final_product", "final_attachment",
            # 输出字段
            "pre_screen_status", "pre_screen_detail",
            "ai_review_status", "ai_review_result",
            # 专家能力分
            "task_complexity_score", "iteration_quality_score",
            "professional_judgment_score", "expert_ability_total",
            # Trace 资产分
            "trace_asset_total", "authenticity_score", "info_density_score",
            "tool_loop_score", "correction_value_score",
            "verification_loop_score", "compliance_score",
            # 最终结论
            "final_conclusion",
        ]
        for name in required_logical_names:
            self.assertIn(name, fm, f"field_mapping 缺少: {name}")
            self.assertTrue(fm[name], f"field_mapping['{name}'] 为空")

    def test_ai_review_files_exist(self):
        """prompt.md 和 schema.json 必须存在"""
        config = self._load_config()
        project_dir = os.path.join(os.path.dirname(__file__), "..", "projects", "expert_review")
        prompt_file = os.path.join(project_dir, config["ai_review"]["prompt_file"])
        schema_file = os.path.join(project_dir, config["ai_review"]["schema_file"])
        self.assertTrue(os.path.isfile(prompt_file), f"prompt 不存在: {prompt_file}")
        self.assertTrue(os.path.isfile(schema_file), f"schema 不存在: {schema_file}")

    def test_exit_code_handling_matches_original_bash(self):
        """验证退出码行为与原 bash 脚本一致"""
        config = self._load_config()
        stages = {s["name"]: s for s in config["stages"]}

        # pre_screen: 0=continue, 1=stop, 2=continue (原 bash: 0→继续, 1→结束, 2→继续)
        ps = stages["pre_screen"]["exit_code_handling"]
        self.assertEqual(ps[0], "continue")
        self.assertEqual(ps[1], "stop")
        self.assertEqual(ps[2], "continue")

        # ai_review: 0=continue, 1=continue (原 bash: 失败不致命)
        ai = stages["ai_review"]["exit_code_handling"]
        self.assertEqual(ai[0], "continue")
        self.assertEqual(ai[1], "continue")


if __name__ == "__main__":
    unittest.main()
