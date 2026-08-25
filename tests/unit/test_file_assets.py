"""M2 文件资产上传和文件 Profile 单元测试。"""

from io import BytesIO

import pytest

from etl_agent.infrastructure.file_profiling import FileProfileError, inspect_upload


def test_csv_inspection_hashes_and_redacts_email() -> None:
    """验证 CSV 文件会计算摘要、清理文件名并脱敏邮箱样本。"""
    inspection = inspect_upload(
        BytesIO(b"email,name\nperson@example.com,Alice\n"),
        "../contacts.csv",
        "text/csv",
        max_size_bytes=1024,
    )

    assert inspection.original_filename == "contacts.csv"
    assert inspection.file_format == "csv"
    assert inspection.size_bytes > 0
    assert len(inspection.sha256) == 64
    assert inspection.schema_snapshot["sample_rows"][0]["email"] == "[REDACTED]"
    inspection.fileobj.close()


def test_json_inspection_infers_fields() -> None:
    """验证 JSON 对象数组会生成字段和样本 Profile。"""
    inspection = inspect_upload(
        BytesIO(b'[{"id": 1, "active": true}]'),
        "records.json",
        "application/json",
        max_size_bytes=1024,
    )

    columns = {
        column["name"]: column["inferred_type"] for column in inspection.schema_snapshot["columns"]
    }
    assert columns == {"id": "integer", "active": "boolean"}
    inspection.fileobj.close()


def test_upload_size_and_extension_are_rejected() -> None:
    """验证超过大小限制或不支持的后缀不会进入对象存储。"""
    with pytest.raises(FileProfileError, match="大小限制"):
        inspect_upload(BytesIO(b"a,b\n1,2\n"), "data.csv", "text/csv", max_size_bytes=2)
    with pytest.raises(FileProfileError, match="仅支持"):
        inspect_upload(BytesIO(b"content"), "data.exe", "application/octet-stream", 1024)
