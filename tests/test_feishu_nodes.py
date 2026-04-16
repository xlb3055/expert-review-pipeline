#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
飞书通用节点测试

覆盖:
- core.feishu_nodes 纯函数
- scripts/feishu_fetch_node.py
- scripts/feishu_write_node.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_script_module(module_name: str, relative_path: str):
    root = os.path.join(os.path.dirname(__file__), "..")
    full_path = os.path.abspath(os.path.join(root, relative_path))
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestFeishuNodesCore(unittest.TestCase):

    def test_load_json_object_from_text(self):
        from core.feishu_nodes import load_json_object

        obj = load_json_object('{"title":"标题","content":"内容"}', label="fields")
        self.assertEqual(obj["title"], "标题")
        self.assertEqual(obj["content"], "内容")

    def test_load_json_object_from_file(self):
        from core.feishu_nodes import load_json_object

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write('{"review_status":"审核状态"}')
            path = f.name

        try:
            obj = load_json_object(file_path=path, label="field_mapping")
            self.assertEqual(obj["review_status"], "审核状态")
        finally:
            os.unlink(path)

    def test_load_json_value_list(self):
        from core.feishu_nodes import load_json_value

        value = load_json_value('[{"record_id":"rec_1"}]', label="batch_data")
        self.assertIsInstance(value, list)
        self.assertEqual(value[0]["record_id"], "rec_1")

    def test_build_ctx_data_from_fields(self):
        from core.feishu_nodes import build_ctx_data_from_fields

        fields = {
            "标题": "一篇文章",
            "作者": [{"name": "张三"}],
            "Trace 文件": [{"file_token": "file_x"}],
        }
        data = build_ctx_data_from_fields(
            fields,
            {"title": "标题", "author": "作者", "trace_file": "Trace 文件"},
            meta={"record_id": "rec_1"},
        )

        self.assertEqual(data["title"], "一篇文章")
        self.assertEqual(data["author"], "张三")
        self.assertIn("_raw_trace_file", data)
        self.assertEqual(data["_meta"]["record_id"], "rec_1")

    def test_extract_data_payload(self):
        from core.feishu_nodes import extract_data_payload

        flat = {"review_status": "pass"}
        nested = {"data": {"review_status": "reject"}}

        self.assertEqual(extract_data_payload(flat)["review_status"], "pass")
        self.assertEqual(extract_data_payload(nested)["review_status"], "reject")

    def test_build_update_fields(self):
        from core.feishu_nodes import build_update_fields

        update_fields = build_update_fields(
            {"review_status": "pass", "note": "通过"},
            {"review_status": "审核状态", "note": "机审说明"},
            status_mapping={"pass": "审核通过"},
        )
        self.assertEqual(update_fields["审核状态"], "审核通过")
        self.assertEqual(update_fields["机审说明"], "通过")

    def test_write_record_from_data(self):
        from core.feishu_nodes import write_record_from_data

        client = MagicMock()
        summary = write_record_from_data(
            client,
            "app_x",
            "tbl_x",
            "rec_x",
            {"review_status": "pass", "note": "ok"},
            {"review_status": "审核状态", "note": "机审说明"},
            status_mapping={"pass": "审核通过"},
        )

        client.update_record.assert_called_once_with(
            "app_x",
            "tbl_x",
            "rec_x",
            {"审核状态": "审核通过", "机审说明": "ok"},
        )
        self.assertEqual(summary["updated_count"], 2)

    def test_query_records_to_data(self):
        from core.feishu_nodes import query_records_to_data

        client = MagicMock()
        client.search_all_records.return_value = [
            {
                "record_id": "rec_1",
                "fields": {
                    "标题": "A",
                    "状态": "待审",
                },
            },
            {
                "record_id": "rec_2",
                "fields": {
                    "标题": "B",
                    "状态": "通过",
                },
            },
        ]

        payload = query_records_to_data(
            client,
            "app_x",
            "tbl_x",
            {"title": "标题", "status": "状态"},
            search_body={"filter": {"conjunction": "and", "conditions": []}},
            page_size=50,
            max_records=2,
        )

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["items"][0]["title"], "A")
        self.assertEqual(payload["items"][1]["status"], "通过")
        self.assertEqual(payload["items"][0]["_meta"]["record_id"], "rec_1")

    def test_extract_batch_items(self):
        from core.feishu_nodes import extract_batch_items

        items = [{"record_id": "rec_1", "status": "pass"}]
        self.assertEqual(len(extract_batch_items(items)), 1)
        self.assertEqual(len(extract_batch_items({"items": items})), 1)
        self.assertEqual(len(extract_batch_items({"records": items})), 1)

    def test_build_batch_update_records(self):
        from core.feishu_nodes import build_batch_update_records

        records = build_batch_update_records(
            [
                {"record_id": "rec_1", "review_status": "pass"},
                {"_meta": {"record_id": "rec_2"}, "data": {"review_status": "reject"}},
            ],
            {"review_status": "审核状态"},
            status_mapping={"pass": "审核通过", "reject": "已拒绝"},
        )

        self.assertEqual(records[0]["fields"]["审核状态"], "审核通过")
        self.assertEqual(records[1]["fields"]["审核状态"], "已拒绝")

    def test_batch_write_records_from_data(self):
        from core.feishu_nodes import batch_write_records_from_data

        client = MagicMock()
        items = [
            {"record_id": "rec_1", "review_status": "pass"},
            {"record_id": "rec_2", "review_status": "reject"},
            {"record_id": "rec_3", "review_status": "pass"},
        ]

        summary = batch_write_records_from_data(
            client,
            "app_x",
            "tbl_x",
            items,
            {"review_status": "审核状态"},
            status_mapping={"pass": "审核通过", "reject": "已拒绝"},
            chunk_size=2,
        )

        self.assertEqual(client.batch_update_records.call_count, 2)
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["chunk_count"], 2)


class TestFeishuFetchNodeScript(unittest.TestCase):

    def test_fetch_node_writes_output_file(self):
        module = _load_script_module("feishu_fetch_node_test", "scripts/feishu_fetch_node.py")
        mock_client = MagicMock()
        mock_client.get_record.return_value = {
            "fields": {
                "标题": "Hello",
                "Trace 文件": [{"file_token": "file_x"}],
            }
        }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_fetch_node.py",
            "--record-id", "rec_123",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--fields-json", '{"title":"标题","trace_file":"Trace 文件"}',
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()

            self.assertEqual(exit_code, 0)
            with open(output_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["title"], "Hello")
            self.assertIn("_raw_trace_file", payload)
            self.assertEqual(payload["_meta"]["record_id"], "rec_123")
        finally:
            os.unlink(output_path)


class TestFeishuWriteNodeScript(unittest.TestCase):

    def test_write_node_updates_record(self):
        module = _load_script_module("feishu_write_node_test", "scripts/feishu_write_node.py")
        mock_client = MagicMock()

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"data": {"review_status": "pass", "review_note": "整体通过"}}, f, ensure_ascii=False)
            data_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_write_node.py",
            "--record-id", "rec_456",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--field-mapping-json", '{"review_status":"审核状态","review_note":"机审说明"}',
            "--status-mapping-json", '{"pass":"审核通过"}',
            "--data-file", data_path,
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()

            self.assertEqual(exit_code, 0)
            mock_client.update_record.assert_called_once_with(
                "app_x",
                "tbl_x",
                "rec_456",
                {"审核状态": "审核通过", "机审说明": "整体通过"},
            )

            with open(output_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            self.assertEqual(summary["updated_count"], 2)
            self.assertFalse(summary["dry_run"])
        finally:
            os.unlink(data_path)
            os.unlink(output_path)


class TestFeishuQueryNodeScript(unittest.TestCase):

    def test_query_node_writes_output_file(self):
        module = _load_script_module("feishu_query_node_test", "scripts/feishu_query_node.py")
        mock_client = MagicMock()
        mock_client.search_all_records.return_value = [
            {
                "record_id": "rec_1",
                "fields": {"标题": "A", "作者": "张三"},
            },
            {
                "record_id": "rec_2",
                "fields": {"标题": "B", "作者": "李四"},
            },
        ]

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_query_node.py",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--fields-json", '{"title":"标题","author":"作者"}',
            "--search-body-json", '{"filter":{"conjunction":"and","conditions":[]}}',
            "--max-records", "2",
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()

            self.assertEqual(exit_code, 0)
            with open(output_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["items"][0]["title"], "A")
            self.assertEqual(payload["items"][1]["author"], "李四")
        finally:
            os.unlink(output_path)


class TestFeishuBatchWriteNodeScript(unittest.TestCase):

    def test_batch_write_node_updates_records(self):
        module = _load_script_module("feishu_batch_write_node_test", "scripts/feishu_batch_write_node.py")
        mock_client = MagicMock()

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(
                [
                    {"record_id": "rec_1", "review_status": "pass"},
                    {"record_id": "rec_2", "review_status": "reject"},
                ],
                f,
                ensure_ascii=False,
            )
            data_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_batch_write_node.py",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--field-mapping-json", '{"review_status":"审核状态"}',
            "--status-mapping-json", '{"pass":"审核通过","reject":"已拒绝"}',
            "--data-file", data_path,
            "--chunk-size", "1",
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_client.batch_update_records.call_count, 2)
            with open(output_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            self.assertEqual(summary["record_count"], 2)
            self.assertEqual(summary["chunk_count"], 2)
        finally:
            os.unlink(data_path)
            os.unlink(output_path)


class TestFeishuReadTemplateScript(unittest.TestCase):

    def test_read_template_record_mode(self):
        module = _load_script_module("feishu_read_template_test", "scripts/feishu_read_template.py")
        mock_client = MagicMock()
        mock_client.get_record.return_value = {
            "fields": {"标题": "单条标题"}
        }

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_read_template.py",
            "--mode", "record",
            "--record-id", "rec_1",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--fields-json", '{"title":"标题"}',
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()
            self.assertEqual(exit_code, 0)
            with open(output_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["title"], "单条标题")
        finally:
            os.unlink(output_path)

    def test_read_template_query_mode(self):
        module = _load_script_module("feishu_read_template_query_test", "scripts/feishu_read_template.py")
        mock_client = MagicMock()
        mock_client.search_all_records.return_value = [
            {"record_id": "rec_1", "fields": {"标题": "A"}},
            {"record_id": "rec_2", "fields": {"标题": "B"}},
        ]

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_read_template.py",
            "--mode", "query",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--fields-json", '{"title":"标题"}',
            "--search-body-json", '{"filter":{"conjunction":"and","conditions":[]}}',
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()
            self.assertEqual(exit_code, 0)
            with open(output_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["count"], 2)
        finally:
            os.unlink(output_path)


class TestFeishuWriteTemplateScript(unittest.TestCase):

    def test_write_template_single_mode(self):
        module = _load_script_module("feishu_write_template_test", "scripts/feishu_write_template.py")
        mock_client = MagicMock()

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"data": {"review_status": "pass"}}, f, ensure_ascii=False)
            data_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_write_template.py",
            "--mode", "single",
            "--record-id", "rec_1",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--field-mapping-json", '{"review_status":"审核状态"}',
            "--status-mapping-json", '{"pass":"审核通过"}',
            "--data-file", data_path,
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()
            self.assertEqual(exit_code, 0)
            mock_client.update_record.assert_called_once()
        finally:
            os.unlink(data_path)
            os.unlink(output_path)

    def test_write_template_batch_mode(self):
        module = _load_script_module("feishu_write_template_batch_test", "scripts/feishu_write_template.py")
        mock_client = MagicMock()

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(
                [{"record_id": "rec_1", "review_status": "pass"}],
                f,
                ensure_ascii=False,
            )
            data_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            output_path = f.name

        argv = [
            "feishu_write_template.py",
            "--mode", "batch",
            "--app-id", "id_x",
            "--app-secret", "secret_x",
            "--app-token", "app_x",
            "--table-id", "tbl_x",
            "--field-mapping-json", '{"review_status":"审核状态"}',
            "--status-mapping-json", '{"pass":"审核通过"}',
            "--data-file", data_path,
            "--output-file", output_path,
        ]

        try:
            with patch.object(module, "FeishuClient", return_value=mock_client), \
                    patch.object(sys, "argv", argv):
                exit_code = module.main()
            self.assertEqual(exit_code, 0)
            mock_client.batch_update_records.assert_called_once()
        finally:
            os.unlink(data_path)
            os.unlink(output_path)


if __name__ == "__main__":
    unittest.main()
