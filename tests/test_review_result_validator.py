#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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

    def test_validate_ok(self):
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
                "tool_loop": {"score": 2},
                "correction_value": {"score": 2},
                "verification_loop": {"score": 2},
                "compliance": {"score": 2},
                "total": 12,
            },
            "overall_assessment": "整体良好",
            "trace_highlights": ["亮点1"],
        }
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_validate_empty_result(self):
        ok, reason = validate_ai_review_result({}, self._expert_dims(), self._trace_dims())
        self.assertFalse(ok)
        self.assertIn("expert_ability", reason)
        self.assertIn("trace_asset", reason)

    def test_validate_error_payload(self):
        ai_result = {
            "error": "无可用评审通道",
            "expert_ability": {"total": 0},
            "trace_asset": {"total": 0},
            "overall_assessment": "失败",
            "trace_highlights": [],
        }
        ok, reason = validate_ai_review_result(ai_result, self._expert_dims(), self._trace_dims())
        self.assertFalse(ok)
        self.assertIn("AI 评审失败", reason)

    def test_invalid_result_messages(self):
        note = _build_invalid_ai_result_note("文件不存在", "/workspace/ai_review_result.json")
        remark = _build_invalid_ai_result_remark()
        self.assertIn("AI评审结果不可用", note)
        self.assertIn("/workspace/ai_review_result.json", note)
        self.assertIn("请人工复核", remark)


if __name__ == "__main__":
    unittest.main()
