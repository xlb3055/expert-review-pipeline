#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

from core.review_result_validator import validate_ai_review_result
from projects.expert_review.writeback import (
    _build_invalid_ai_result_note,
    _build_invalid_ai_result_remark,
)


class TestReviewResultValidator(unittest.TestCase):
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

    def test_valid_ai_review_result(self):
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 2},
                "iteration_quality": {"score": 2},
                "professional_judgment": {"score": 3},
                "total": 7,
            },
            "trace_asset": {
                "authenticity": {"score": 2},
                "info_density": {"score": 2},
                "tool_loop": {"score": 1},
                "correction_value": {"score": 1},
                "verification_loop": {"score": 2},
                "compliance": {"score": 2},
                "total": 10,
            },
            "overall_assessment": "整体较好。",
            "trace_highlights": ["有验证闭环"],
        }
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_empty_ai_review_result_invalid(self):
        ok, reason = validate_ai_review_result({}, self._expert_dims(), self._trace_dims())
        self.assertFalse(ok)
        self.assertIn("expert_ability", reason)

    def test_total_mismatch_invalid(self):
        ai_result = {
            "expert_ability": {
                "task_complexity": {"score": 2},
                "iteration_quality": {"score": 2},
                "professional_judgment": {"score": 3},
                "total": 6,
            },
            "trace_asset": {
                "authenticity": {"score": 2},
                "info_density": {"score": 2},
                "tool_loop": {"score": 1},
                "correction_value": {"score": 1},
                "verification_loop": {"score": 2},
                "compliance": {"score": 2},
                "total": 10,
            },
            "overall_assessment": "整体较好。",
            "trace_highlights": ["有验证闭环"],
        }
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertFalse(ok)
        self.assertIn("total", reason)

    def test_error_field_with_complete_data_passes(self):
        """error 字段存在但打分字段完整时，应通过验证"""
        ai_result = {
            "error": "some residual error from CLI wrapper",
            "expert_ability": {
                "task_complexity": {"score": 2},
                "iteration_quality": {"score": 2},
                "professional_judgment": {"score": 3},
                "total": 7,
            },
            "trace_asset": {
                "authenticity": {"score": 2},
                "info_density": {"score": 2},
                "tool_loop": {"score": 1},
                "correction_value": {"score": 1},
                "verification_loop": {"score": 2},
                "compliance": {"score": 2},
                "total": 10,
            },
            "overall_assessment": "整体较好。",
            "trace_highlights": ["有验证闭环"],
        }
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertTrue(ok, f"应通过验证，但失败: {reason}")

    def test_error_field_without_core_fields_fails(self):
        """error 字段存在且缺少打分字段时，应判定失败"""
        ai_result = {"error": "AI review failed completely"}
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertFalse(ok)
        self.assertIn("错误", reason)

    def test_invalid_result_note_and_remark(self):
        note = _build_invalid_ai_result_note("文件不存在", "/workspace/ai_review_result.json")
        remark = _build_invalid_ai_result_remark()
        self.assertIn("/workspace/ai_review_result.json", note)
        self.assertIn("人工复核", note)
        self.assertIn("人工复核", remark)


if __name__ == "__main__":
    unittest.main()
