from __future__ import annotations

import base64
import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..config import Settings
from ..ocr_contract import OCR_OUTPUT_SCHEMA, OCR_PROMPT
from ..services.import_parsers import ParseOutcome
from ..services.ocr_results import OcrResultError, parse_ocr_payload, parse_ocr_result


@dataclass(frozen=True, slots=True)
class BillOcrChunkRequest:
    source_file_id: str
    batch_id: str
    filename: str
    encrypted_path: Path
    page_start: int
    page_end: int


class BillOcrProvider(Protocol):
    """Pluggable multimodal bill-recognition provider returning unconfirmed candidates."""

    provider_id: str
    display_name: str

    def extract_chunk(
        self,
        *,
        chunk: BillOcrChunkRequest,
        payload: bytes,
        settings: Settings,
    ) -> ParseOutcome: ...


PrepareJobFn = Callable[..., Path]
WaitForResultFn = Callable[[Path, Settings], Path]
RenderPagesFn = Callable[[BillOcrChunkRequest, bytes, Path, Settings], list[Path]]


class CodexCliOcrProvider:
    """Isolated tmpfs queue provider backed by the read-only Codex CLI runner."""

    provider_id = "codex-cli"
    display_name = "Codex OCR"

    def __init__(
        self,
        *,
        prepare_job_fn: PrepareJobFn,
        wait_for_result_fn: WaitForResultFn,
    ) -> None:
        self._prepare_job = prepare_job_fn
        self._wait_for_result = wait_for_result_fn

    def extract_chunk(
        self,
        *,
        chunk: BillOcrChunkRequest,
        payload: bytes,
        settings: Settings,
    ) -> ParseOutcome:
        job_dir = self._prepare_job(
            chunk,
            payload,
            settings,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
        )
        try:
            result_path = self._wait_for_result(job_dir, settings)
            return parse_ocr_result(
                result_path,
                max_transactions=settings.ocr_max_transactions,
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)


class OpenAiVisionOcrProvider:
    """OpenAI-compatible multimodal HTTP provider with identical schema validation."""

    provider_id = "openai-vision"
    display_name = "OpenAI Vision OCR"

    def __init__(
        self,
        *,
        render_pages_fn: RenderPagesFn,
        transport_fn: Callable[[str, dict[str, object], str, int], dict[str, object]] | None = None,
    ) -> None:
        self._render_pages = render_pages_fn
        self._transport = transport_fn or _post_json

    def extract_chunk(
        self,
        *,
        chunk: BillOcrChunkRequest,
        payload: bytes,
        settings: Settings,
    ) -> ParseOutcome:
        base_url = (settings.ocr_api_base_url or "").strip().rstrip("/")
        api_key = (settings.ocr_api_key or "").strip()
        if not base_url or not api_key:
            raise ValueError("OpenAI Vision OCR 缺少 API 地址或密钥配置")

        with tempfile.TemporaryDirectory(prefix="homedatabase-vision-ocr-") as temp_dir:
            images = self._render_pages(chunk, payload, Path(temp_dir), settings)
            image_names = "、".join(image.name for image in images)
            prompt = (
                f"{OCR_PROMPT}\n\n本次附件文件名依次为：{image_names}。"
                "page-NNN 中的 NNN 是整份原文件的真实页码；sourceRef 必须沿用该真实页码，"
                "不要按本次附件顺序从第1页重新编号。"
            )
            content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
            for image in images:
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    }
                )

        request_body: dict[str, object] = {
            "model": settings.ocr_model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "homedatabase_bill_ocr",
                    "strict": True,
                    "schema": OCR_OUTPUT_SCHEMA,
                },
            },
        }
        response = self._transport(
            f"{base_url}/chat/completions",
            request_body,
            api_key,
            settings.ocr_timeout_seconds,
        )
        raw_text = _extract_assistant_content(response)
        try:
            decoded = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise OcrResultError("OCR 返回的数据格式无效") from exc
        return parse_ocr_payload(
            decoded,
            max_transactions=settings.ocr_max_transactions,
            provider_label=self.display_name,
        )


def _post_json(
    url: str,
    body: dict[str, object],
    api_key: str,
    timeout_seconds: int,
) -> dict[str, object]:
    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ValueError("OCR 远端服务必须使用 HTTPS（本机回环地址除外）")
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read(2 * 1024 * 1024 + 1)
    except urllib_error.HTTPError as exc:
        raise RuntimeError(f"视觉识别接口返回 HTTP {exc.code}") from exc
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("视觉识别接口连接失败或超时") from exc
    if len(raw) > 2 * 1024 * 1024:
        raise OcrResultError("OCR 结果超过安全大小限制")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OcrResultError("OCR 返回的数据格式无效") from exc
    if not isinstance(parsed, dict):
        raise OcrResultError("OCR 返回的数据格式无效")
    return parsed


def _extract_assistant_content(response: dict[str, object]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OcrResultError("OCR 返回的数据格式无效")
    first = choices[0]
    if not isinstance(first, dict):
        raise OcrResultError("OCR 返回的数据格式无效")
    message = first.get("message")
    if not isinstance(message, dict):
        raise OcrResultError("OCR 返回的数据格式无效")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OcrResultError("OCR 返回的数据格式无效")
    return content


_CUSTOM_OCR_PROVIDERS: dict[str, BillOcrProvider] = {}


def register_ocr_provider(provider: BillOcrProvider) -> None:
    """Register a custom BillOcrProvider plugin (for local VLM adapters or tests)."""
    _CUSTOM_OCR_PROVIDERS[provider.provider_id] = provider


def get_ocr_provider(
    settings: Settings,
    *,
    prepare_job_fn: PrepareJobFn,
    wait_for_result_fn: WaitForResultFn,
    render_pages_fn: RenderPagesFn,
) -> BillOcrProvider:
    custom = _CUSTOM_OCR_PROVIDERS.get(settings.ocr_provider)
    if custom is not None:
        return custom
    if settings.ocr_provider == "openai-vision":
        return OpenAiVisionOcrProvider(render_pages_fn=render_pages_fn)
    return CodexCliOcrProvider(
        prepare_job_fn=prepare_job_fn,
        wait_for_result_fn=wait_for_result_fn,
    )
