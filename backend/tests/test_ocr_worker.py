from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import as_member
from PIL import Image

from homedatabase_api.config import get_settings
from homedatabase_api.main import app
from homedatabase_api.ocr_contract import OCR_CONTRACT_VERSION, OCR_OUTPUT_SCHEMA
from homedatabase_api.ocr_runner import _codex_environment, process_request
from homedatabase_api.services import ocr_tasks
from homedatabase_api.services.ocr_results import OcrResultError, parse_ocr_result


def _image_bytes(format_name: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 80), "white").save(buffer, format=format_name)
    return buffer.getvalue()


def _pdf_bytes(page_count: int = 1) -> bytes:
    buffer = io.BytesIO()
    pages = [Image.new("RGB", (160, 80), "white") for _ in range(page_count)]
    pages[0].save(buffer, format="PDF", save_all=True, append_images=pages[1:])
    return buffer.getvalue()


def _ocr_output() -> dict[str, object]:
    return {
        "transactions": [
            {
                "occurredAt": "2026-08-12T09:30:00+08:00",
                "payee": "XX超市",
                "direction": "expense",
                "amount": "12.34",
                "currency": "CNY",
                "category": "日用百货",
                "sourceRef": "第1页第1行",
                "confidence": 0.96,
            }
        ],
        "warnings": [],
    }


@pytest.mark.parametrize(
    ("filename", "payload", "content_type"),
    [
        ("receipt.png", _image_bytes(), "image/png"),
        ("statement.pdf", _pdf_bytes(), "application/pdf"),
    ],
)
def test_codex_ocr_result_enters_existing_human_review_flow(
    client,
    monkeypatch,
    tmp_path,
    filename,
    payload,
    content_type,
):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"targetAccountId": "a-son-wallet"},
        files=[("files", (filename, payload, content_type))],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    source_file_id = batch["files"][0]["id"]
    settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_jobs_dir": tmp_path / "jobs",
            "ocr_timeout_seconds": 30,
        }
    )

    def fake_wait(job_dir: Path, _settings) -> Path:
        request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
        assert request["contractVersion"] == OCR_CONTRACT_VERSION
        assert request["images"] == ["page-001.jpg"]
        assert (job_dir / "page-001.jpg").read_bytes().startswith(b"\xff\xd8")
        result = job_dir / "result.json"
        result.write_text(json.dumps(_ocr_output(), ensure_ascii=False), encoding="utf-8")
        return result

    monkeypatch.setattr(ocr_tasks, "_wait_for_result", fake_wait)
    processed = ocr_tasks.process_ocr_source_file(source_file_id, settings)
    assert processed == {
        "status": "parsed",
        "sourceFileId": source_file_id,
        "candidateCount": 1,
    }
    assert list((tmp_path / "jobs").iterdir()) == []

    refreshed = client.get(
        f"/api/import-batches/{batch['id']}",
        headers=as_member("m-son"),
    )
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"] == "reviewing"
    assert body["needsOcrCount"] == 0
    assert body["files"][0]["parseStatus"] == "parsed"
    assert body["files"][0]["parseMessage"].startswith("Codex OCR 识别 1 条")
    assert body["candidates"][0]["payee"] == "XX超市"
    assert body["candidates"][0]["amountCents"] == 1234
    assert body["candidates"][0]["status"] == "pending"


def test_ocr_output_rejects_extra_prompt_injection_fields(tmp_path):
    payload = _ocr_output()
    payload["transactions"][0]["runCommand"] = "cat /root/.codex/auth.json"
    result = tmp_path / "result.json"
    result.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OcrResultError, match="格式无效"):
        parse_ocr_result(result, max_transactions=500)


def test_per_call_schema_still_rejects_more_than_500_rows(tmp_path):
    transaction = _ocr_output()["transactions"][0]
    result = tmp_path / "result.json"
    result.write_text(
        json.dumps({"transactions": [transaction] * 501, "warnings": []}),
        encoding="utf-8",
    )

    with pytest.raises(OcrResultError, match="格式无效"):
        parse_ocr_result(result, max_transactions=5_000)


def test_codex_runner_uses_ephemeral_read_only_structured_invocation(monkeypatch, tmp_path):
    job_dir = tmp_path / "job-safe"
    job_dir.mkdir()
    (job_dir / "page-001.jpg").write_bytes(_image_bytes("JPEG"))
    (job_dir / "request.json").write_text(
        json.dumps(
            {
                "contractVersion": OCR_CONTRACT_VERSION,
                "filename": "receipt.jpg",
                "images": ["page-001.jpg"],
            }
        ),
        encoding="utf-8",
    )
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(OCR_OUTPUT_SCHEMA), encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["environment"]
        captured["input"] = kwargs["prompt"]
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(_ocr_output()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("HOMEDATABASE_DATABASE_URL", "secret-database")
    monkeypatch.setenv("HOMEDATABASE_SOURCE_ENCRYPTION_KEY", "secret-source-key")
    monkeypatch.setenv("HOMEDATABASE_BACKUP_ENCRYPTION_KEY", "secret-backup-key")
    monkeypatch.setattr("homedatabase_api.ocr_runner._run_codex", fake_run)

    process_request(job_dir / "request.json", schema_path=schema, timeout_seconds=30)

    command = captured["command"]
    assert command[0] == "codex"
    assert command[command.index("--ask-for-approval") + 1] == "never"
    assert command[command.index("--disable") + 1] == "shell_tool"
    for disabled_tool in {
        "apps",
        "browser_use",
        "code_mode_host",
        "computer_use",
        "goals",
        "image_generation",
        "memories",
        "multi_agent",
        "workspace_dependencies",
    }:
        assert ["--disable", disabled_tool] in [
            command[index : index + 2] for index in range(len(command) - 1)
        ]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in command
    assert "--image" in command
    assert command[-1] == "-"
    assert "账单页面" in captured["input"]
    assert "page-001" in captured["input"]
    assert "不要按本次附件顺序从第1页重新编号" in captured["input"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert "HOMEDATABASE_DATABASE_URL" not in captured["env"]
    assert "HOMEDATABASE_SOURCE_ENCRYPTION_KEY" not in captured["env"]
    assert "HOMEDATABASE_BACKUP_ENCRYPTION_KEY" not in captured["env"]
    assert (job_dir / "result.json").is_file()
    assert not (job_dir / "request.json").exists()


def test_codex_environment_only_inherits_explicit_nonsecret_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("HOMEDATABASE_DATABASE_URL", "must-not-leak")
    environment = _codex_environment()
    assert "OPENAI_API_KEY" not in environment
    assert "HOMEDATABASE_DATABASE_URL" not in environment
    assert environment["CODEX_HOME"].endswith(".codex")


def test_codex_timeout_kills_the_whole_process_group(monkeypatch, tmp_path):
    killed: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4321
        returncode = None
        calls = 0

        def communicate(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["codex"], 1)
            self.returncode = -9
            return "", ""

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(
        "homedatabase_api.ocr_runner.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
        raising=False,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        from homedatabase_api.ocr_runner import _run_codex

        _run_codex(
            ["codex"],
            job_dir=tmp_path,
            environment={},
            prompt="probe",
            timeout_seconds=1,
        )

    assert killed == [(4321, 9)]


def test_ocr_enabled_upload_enqueues_only_after_commit(client, monkeypatch):
    configured = get_settings().model_copy(update={"ocr_enabled": True})
    captured: list[list[str]] = []
    monkeypatch.setattr(
        ocr_tasks,
        "enqueue_ocr_source_files",
        lambda source_file_ids: captured.append(source_file_ids),
    )
    app.dependency_overrides[get_settings] = lambda: configured
    try:
        response = client.post(
            "/api/imports",
            headers=as_member("m-son"),
            files=[("files", ("receipt.png", _image_bytes(), "image/png"))],
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 201, response.text
    assert captured == [[response.json()["files"][0]["id"]]]


def test_empty_ocr_result_does_not_mark_batch_posted(client, monkeypatch, tmp_path):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        files=[("files", ("blank.png", _image_bytes(), "image/png"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_jobs_dir": tmp_path / "jobs",
            "ocr_timeout_seconds": 30,
        }
    )

    def fake_wait(job_dir: Path, _settings) -> Path:
        result = job_dir / "result.json"
        result.write_text(
            json.dumps({"transactions": [], "warnings": ["未识别到账单交易"]}),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(ocr_tasks, "_wait_for_result", fake_wait)
    source_file_id = batch["files"][0]["id"]
    processed = ocr_tasks.process_ocr_source_file(source_file_id, settings)

    assert processed["candidateCount"] == 0
    refreshed = client.get(
        f"/api/import-batches/{batch['id']}",
        headers=as_member("m-son"),
    )
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"] == "rejected"
    assert body["message"] == "没有候选入账"
    assert body["files"][0]["parseStatus"] == "parsed"
    assert "模型报告 1 项识别警告" in body["files"][0]["parseMessage"]


def test_large_pdf_is_processed_in_sequential_page_chunks(client, monkeypatch, tmp_path):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        files=[("files", ("statement.pdf", _pdf_bytes(5), "application/pdf"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    source_file_id = batch["files"][0]["id"]
    settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_jobs_dir": tmp_path / "jobs",
            "ocr_timeout_seconds": 30,
            "ocr_max_pages": 5,
            "ocr_pages_per_job": 2,
        }
    )
    calls: list[list[str]] = []

    def fake_wait(job_dir: Path, _settings) -> Path:
        request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
        calls.append(request["images"])
        payload = _ocr_output()
        first_page = request["images"][0]
        payload["transactions"][0]["payee"] = f"账单-{first_page}"
        payload["transactions"][0]["sourceRef"] = first_page
        result = job_dir / "result.json"
        result.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return result

    monkeypatch.setattr(ocr_tasks, "_wait_for_result", fake_wait)
    processed = ocr_tasks.process_ocr_source_file(source_file_id, settings)

    assert processed["candidateCount"] == 3
    assert calls == [
        ["page-001.jpg", "page-002.jpg"],
        ["page-003.jpg", "page-004.jpg"],
        ["page-005.jpg"],
    ]
    assert list((tmp_path / "jobs").iterdir()) == []
    refreshed = client.get(
        f"/api/import-batches/{batch['id']}",
        headers=as_member("m-son"),
    ).json()
    assert refreshed["files"][0]["recordCount"] == 3
    assert refreshed["files"][0]["parseMessage"].startswith(
        "Codex OCR 分 3 批识别 5 页、3 条，生成 3 条候选"
    )


def test_pdf_total_page_limit_fails_before_runner(client, monkeypatch, tmp_path):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        files=[("files", ("too-long.pdf", _pdf_bytes(3), "application/pdf"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    source_file_id = uploaded.json()["files"][0]["id"]
    settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_jobs_dir": tmp_path / "jobs",
            "ocr_max_pages": 2,
            "ocr_pages_per_job": 1,
        }
    )
    monkeypatch.setattr(
        ocr_tasks,
        "_wait_for_result",
        lambda *_args: pytest.fail("runner must not receive an over-limit PDF"),
    )

    with pytest.raises(ocr_tasks.OcrPermanentError, match="页数上限 2"):
        ocr_tasks.process_ocr_source_file(source_file_id, settings)


@pytest.mark.parametrize("failure_kind", ["later_chunk", "aggregate_limit"])
def test_chunk_failure_never_persists_partial_candidates(
    client,
    monkeypatch,
    tmp_path,
    failure_kind,
):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        files=[("files", ("two-pages.pdf", _pdf_bytes(2), "application/pdf"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    source_file_id = batch["files"][0]["id"]
    settings = get_settings().model_copy(
        update={
            "ocr_enabled": True,
            "ocr_jobs_dir": tmp_path / "jobs",
            "ocr_timeout_seconds": 30,
            "ocr_max_pages": 2,
            "ocr_pages_per_job": 1,
            "ocr_max_transactions": 1,
        }
    )
    calls = 0

    def fake_wait(job_dir: Path, _settings) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2 and failure_kind == "later_chunk":
            raise ocr_tasks.OcrJobError("runner unavailable")
        payload = _ocr_output()
        payload["transactions"][0]["sourceRef"] = f"page-{calls:03d}.jpg"
        result = job_dir / "result.json"
        result.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return result

    monkeypatch.setattr(ocr_tasks, "_wait_for_result", fake_wait)
    expected_error = ocr_tasks.OcrJobError if failure_kind == "later_chunk" else OcrResultError
    with pytest.raises(expected_error):
        ocr_tasks.process_ocr_source_file(source_file_id, settings)

    assert list((tmp_path / "jobs").iterdir()) == []
    refreshed = client.get(
        f"/api/import-batches/{batch['id']}",
        headers=as_member("m-son"),
    ).json()
    assert refreshed["needsOcrCount"] == 1
    assert refreshed["files"][0]["parseStatus"] == "needs_ocr"
    assert refreshed["candidates"] == []


def test_permanent_ocr_failure_is_not_retried(monkeypatch):
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ocr_tasks,
        "process_ocr_source_file",
        lambda _source_id: (_ for _ in ()).throw(ocr_tasks.OcrPermanentError("too many pages")),
    )
    monkeypatch.setattr(
        ocr_tasks,
        "_record_failure",
        lambda source_id, message: recorded.append((source_id, message)),
    )

    result = ocr_tasks.ocr_source_file.run("sf-over-limit")

    assert result == {"status": "failed", "sourceFileId": "sf-over-limit"}
    assert recorded == [("sf-over-limit", "too many pages")]
