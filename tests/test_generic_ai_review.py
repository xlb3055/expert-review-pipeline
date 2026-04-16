#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.generic_ai_review import (
    GenericAIReviewConfigError,
    GenericAIReviewExecutionError,
    GenericAIReviewOutcome,
    GenericAIReviewRequest,
    _auto_fill_totals,
    normalize_schema_payload,
    resolve_request_from_sources,
    run_generic_ai_review,
    unwrap_schema_envelope,
)


class TestGenericAIReviewRequestResolution(unittest.TestCase):
    def _write_file(self, content: str) -> str:
        fd, path = tempfile.mkstemp()
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def _build_args(self, **overrides):
        base = {
            "prompt_file": None,
            "prompt_text": None,
            "schema_file": None,
            "schema_text": None,
            "input_file": None,
            "input_text": None,
            "output_path": None,
            "error_output_path": None,
            "model": None,
            "mode": None,
            "timeout": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_cli_file_overrides_env_text(self):
        prompt_file = self._write_file("file prompt")
        schema_file = self._write_file('{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}')
        input_file = self._write_file("file input")

        args = self._build_args(
            prompt_file=prompt_file,
            schema_file=schema_file,
            input_file=input_file,
        )
        env = {
            "AI_REVIEW_PROMPT": "env prompt",
            "AI_REVIEW_OUTPUT_PATH": "/tmp/out.json",
            "AI_REVIEW_ERROR_PATH": "/tmp/err.json",
        }

        request = resolve_request_from_sources(args, env)
        self.assertEqual(request.prompt_text, "file prompt")
        self.assertEqual(request.input_text, "file input")

    def test_conflict_in_same_source_rejected(self):
        args = self._build_args(prompt_file="/tmp/prompt.md", prompt_text="hello")
        with self.assertRaises(GenericAIReviewConfigError):
            resolve_request_from_sources(args, {"AI_REVIEW_OUTPUT_PATH": "/tmp/out", "AI_REVIEW_ERROR_PATH": "/tmp/err"})

    def test_env_text_mode_supported(self):
        args = self._build_args()
        env = {
            "AI_REVIEW_PROMPT": "prompt text",
            "AI_REVIEW_SCHEMA": '{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}',
            "AI_REVIEW_INPUT": "input text",
            "AI_REVIEW_OUTPUT_PATH": "/tmp/out.json",
            "AI_REVIEW_ERROR_PATH": "/tmp/err.json",
            "AI_REVIEW_MODEL": "test-model",
            "AI_REVIEW_MODE": "api",
            "AI_REVIEW_TIMEOUT": "42",
        }
        request = resolve_request_from_sources(args, env)
        self.assertEqual(request.model, "test-model")
        self.assertEqual(request.mode, "api")
        self.assertEqual(request.timeout, 42)
        self.assertEqual(request.input_text, "input text")


class TestGenericAIReviewSchemaHelpers(unittest.TestCase):
    def test_normalize_wrapped_schema(self):
        schema = normalize_schema_payload('{"name":"demo_result","strict":true,"schema":{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}}')
        self.assertEqual(schema["name"], "demo_result")
        self.assertIn("score", schema["schema"]["properties"])

    def test_normalize_raw_schema(self):
        schema = normalize_schema_payload('{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}')
        self.assertEqual(schema["name"], "ai_review_result")
        self.assertEqual(schema["schema"]["type"], "object")

    def test_unwrap_schema_envelope(self):
        schema = normalize_schema_payload('{"name":"demo_result","schema":{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}}')
        result = unwrap_schema_envelope({"demo_result": {"score": 8}}, schema)
        self.assertEqual(result, {"score": 8})

    def test_unwrap_cli_type_result_string(self):
        """Claude CLI --output-format json 包装: {"type":"result","result":"{\\"score\\":8}"}"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            }
        }))
        cli_output = {"type": "result", "result": json.dumps({"score": 8})}
        result = unwrap_schema_envelope(cli_output, schema)
        self.assertEqual(result["score"], 8)

    def test_unwrap_cli_double_wrapped(self):
        """CLI 包装 + schema name 包装: {"type":"result","result":"{\\"demo_result\\":{\\"score\\":8}}"}"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            }
        }))
        inner = json.dumps({"demo_result": {"score": 8}})
        cli_output = {"type": "result", "result": inner}
        result = unwrap_schema_envelope(cli_output, schema)
        self.assertEqual(result["score"], 8)

    def test_unwrap_structured_output(self):
        """执行器格式: {"structured_output": {"score": 8}}"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            }
        }))
        result = unwrap_schema_envelope({"structured_output": {"score": 8}}, schema)
        self.assertEqual(result["score"], 8)

    def test_unwrap_deeply_nested(self):
        """三层嵌套: result -> demo_result -> 实际数据"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            }
        }))
        wrapped = {"result": {"demo_result": {"score": 8}}}
        result = unwrap_schema_envelope(wrapped, schema)
        self.assertEqual(result["score"], 8)

    def test_unwrap_strips_extra_fields_when_additional_properties_false(self):
        """additionalProperties=false 时，unwrap 应清理 schema 未声明的多余字段"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
                "additionalProperties": False,
            }
        }))
        # 模拟 CLI 包装层残留了 error 字段
        result = unwrap_schema_envelope({"score": 8, "error": "residual"}, schema)
        self.assertEqual(result, {"score": 8})
        self.assertNotIn("error", result)

    def test_unwrap_keeps_extra_fields_when_additional_properties_allowed(self):
        """additionalProperties 未设为 false 时，不应清理多余字段"""
        schema = normalize_schema_payload(json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "properties": {"score": {"type": "integer"}},
                "required": ["score"],
            }
        }))
        result = unwrap_schema_envelope({"score": 8, "extra": "kept"}, schema)
        self.assertIn("extra", result)

    def test_unwrap_expert_review_real_format(self):
        """模拟真实 expert_review schema 的多层包装场景"""
        schema = normalize_schema_payload(json.dumps({
            "name": "expert_review_result",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "expert_ability": {"type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"]},
                    "trace_asset": {"type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"]},
                    "overall_assessment": {"type": "string"},
                    "trace_highlights": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["expert_ability", "trace_asset", "overall_assessment", "trace_highlights"],
            }
        }))
        actual_data = {
            "expert_ability": {"total": 7},
            "trace_asset": {"total": 9},
            "overall_assessment": "good",
            "trace_highlights": ["a"],
        }
        # 场景1: schema name 包装
        result = unwrap_schema_envelope({"expert_review_result": actual_data.copy()}, schema)
        self.assertEqual(result["expert_ability"]["total"], 7)

        # 场景2: CLI string 包装 + schema name 包装
        cli_output = {"type": "result", "result": json.dumps({"expert_review_result": actual_data})}
        result = unwrap_schema_envelope(cli_output, schema)
        self.assertEqual(result["expert_ability"]["total"], 7)

        # 场景3: 直接是实际数据
        result = unwrap_schema_envelope(actual_data.copy(), schema)
        self.assertEqual(result["expert_ability"]["total"], 7)


class TestAutoFillTotals(unittest.TestCase):
    def test_fills_missing_total(self):
        """模型没返回 total 时，自动根据各维度 score 求和补上"""
        schema = normalize_schema_payload(json.dumps({
            "name": "review",
            "schema": {
                "type": "object",
                "properties": {
                    "expert_ability": {
                        "type": "object",
                        "properties": {
                            "dim_a": {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]},
                            "dim_b": {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]},
                            "total": {"type": "integer"},
                        },
                        "required": ["dim_a", "dim_b", "total"],
                    }
                },
                "required": ["expert_ability"],
            }
        }))
        result = {"expert_ability": {"dim_a": {"score": 3}, "dim_b": {"score": 5}}}
        _auto_fill_totals(result, schema)
        self.assertEqual(result["expert_ability"]["total"], 8)

    def test_does_not_overwrite_existing_total(self):
        """模型已返回 total 时，不应覆盖"""
        schema = normalize_schema_payload(json.dumps({
            "name": "review",
            "schema": {
                "type": "object",
                "properties": {
                    "module": {
                        "type": "object",
                        "properties": {
                            "dim": {"type": "object", "properties": {"score": {"type": "integer"}}, "required": ["score"]},
                            "total": {"type": "integer"},
                        },
                        "required": ["dim", "total"],
                    }
                },
                "required": ["module"],
            }
        }))
        result = {"module": {"dim": {"score": 3}, "total": 99}}
        _auto_fill_totals(result, schema)
        self.assertEqual(result["module"]["total"], 99)

    def test_run_success_auto_fills_total(self):
        """端到端：模型输出缺 total 时，run_generic_ai_review 自动补上后通过校验"""
        schema_text = json.dumps({
            "name": "demo_result",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "module": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "dim_a": {"type": "object", "additionalProperties": False, "properties": {"score": {"type": "integer"}}, "required": ["score"]},
                            "dim_b": {"type": "object", "additionalProperties": False, "properties": {"score": {"type": "integer"}}, "required": ["score"]},
                            "total": {"type": "integer"},
                        },
                        "required": ["dim_a", "dim_b", "total"],
                    }
                },
                "required": ["module"],
            }
        })
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(tmpdir) and __import__("shutil").rmtree(tmpdir))
        request = GenericAIReviewRequest(
            prompt_text="prompt", schema_text=schema_text, input_text="input",
            output_path=os.path.join(tmpdir, "result.json"),
            error_output_path=os.path.join(tmpdir, "error.json"),
            mode="api",
        )
        # 模型输出没有 total
        with patch(
            "core.generic_ai_review._execute_ai_review",
            return_value=GenericAIReviewOutcome(
                success=True,
                result_json={"demo_result": {"module": {"dim_a": {"score": 2}, "dim_b": {"score": 3}}}},
                mode_used="api",
            ),
        ):
            outcome = run_generic_ai_review(request)

        self.assertTrue(outcome.success, f"应成功但失败: {outcome.error}")
        with open(request.output_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["module"]["total"], 5)


class TestGenericAIReviewExecution(unittest.TestCase):
    def _request(self, schema_text: str) -> GenericAIReviewRequest:
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.isdir(tmpdir) and __import__("shutil").rmtree(tmpdir))
        return GenericAIReviewRequest(
            prompt_text="prompt",
            schema_text=schema_text,
            input_text="input",
            output_path=os.path.join(tmpdir, "result.json"),
            error_output_path=os.path.join(tmpdir, "error.json"),
            mode="api",
        )

    def test_run_success_writes_output(self):
        request = self._request(
            '{"name":"demo_result","schema":{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}}'
        )
        stale_error = request.error_output_path
        with open(stale_error, "w", encoding="utf-8") as f:
            f.write("stale")

        with patch(
            "core.generic_ai_review._execute_ai_review",
            return_value=GenericAIReviewOutcome(success=True, result_json={"demo_result": {"score": 8}}, mode_used="api"),
        ):
            outcome = run_generic_ai_review(request)

        self.assertTrue(outcome.success)
        with open(request.output_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"score": 8})
        self.assertFalse(os.path.exists(stale_error))

    def test_failure_writes_error_only(self):
        request = self._request(
            '{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"]}'
        )
        with open(request.output_path, "w", encoding="utf-8") as f:
            json.dump({"existing": True}, f)

        with patch(
            "core.generic_ai_review._execute_ai_review",
            side_effect=GenericAIReviewExecutionError("boom"),
        ):
            outcome = run_generic_ai_review(request)

        self.assertFalse(outcome.success)
        with open(request.output_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"existing": True})
        with open(request.error_output_path, encoding="utf-8") as f:
            error_payload = json.load(f)
        self.assertEqual(error_payload["error"], "boom")

    def test_schema_validation_failure_writes_error(self):
        request = self._request(
            '{"type":"object","properties":{"score":{"type":"integer"}},"required":["score"],"additionalProperties":false}'
        )
        with patch(
            "core.generic_ai_review._execute_ai_review",
            return_value=GenericAIReviewOutcome(success=True, result_json={"detail": "missing score"}, mode_used="api"),
        ):
            outcome = run_generic_ai_review(request)

        self.assertFalse(outcome.success)
        self.assertFalse(os.path.exists(request.output_path))
        with open(request.error_output_path, encoding="utf-8") as f:
            error_payload = json.load(f)
        self.assertIn("score", error_payload["error"].lower())


if __name__ == "__main__":
    unittest.main()
