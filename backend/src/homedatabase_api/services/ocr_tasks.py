from __future__ import annotations

import io
import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from PIL.Image import DecompressionBombWarning
from pillow_heif import register_heif_opener
from pypdfium2 import PdfDocument
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..celery_app import celery_app
from ..config import Settings, get_settings
from ..database import get_session_factory
from ..models import ImportBatch, Member, SourceFile
from ..ocr_contract import OCR_CONTRACT_VERSION
from .audit import record_event
from .import_parsers import ParseOutcome
from .ocr_results import OcrResultError, parse_ocr_result
from .source_storage import decrypt_source_file

logger = logging.getLogger(__name__)
register_heif_opener()


class OcrJobError(RuntimeError):
    pass


class OcrPermanentError(OcrJobError):
    """An input/configuration failure that retrying cannot repair."""


@dataclass(frozen=True, slots=True)
class _SourceSnapshot:
    source_file_id: str
    batch_id: str
    filename: str
    encrypted_path: Path


def enqueue_ocr_source_files(source_file_ids: list[str]) -> None:
    for source_file_id in source_file_ids:
        try:
            ocr_source_file.apply_async(args=[source_file_id], queue="ocr")
        except Exception:
            # The upload is already committed and remains visibly needs_ocr.
            # Broker recovery or an operator can safely enqueue the id again.
            logger.exception("failed to enqueue OCR source file %s", source_file_id)


def _source_snapshot(source_file_id: str, settings: Settings) -> _SourceSnapshot | None:
    with get_session_factory()() as db:
        source_file = db.scalar(
            select(SourceFile)
            .where(SourceFile.id == source_file_id)
            .options(selectinload(SourceFile.batch))
        )
        if not source_file or source_file.parse_status != "needs_ocr":
            return None
        if source_file.batch.status not in {"reviewing", "needs_ocr"}:
            return None
        if not source_file.encrypted_path:
            raise OcrPermanentError("OCR 源文件密文不存在")
        archive_dir = settings.source_archive_dir.resolve()
        encrypted_path = (archive_dir / Path(source_file.encrypted_path).name).resolve()
        if encrypted_path.parent != archive_dir or not encrypted_path.is_file():
            raise OcrPermanentError("OCR 源文件密文不存在")
        return _SourceSnapshot(
            source_file_id=source_file.id,
            batch_id=source_file.batch_id,
            filename=source_file.filename,
            encrypted_path=encrypted_path,
        )


def _save_rgb_jpeg(image: Image.Image, target: Path, *, max_pixels: int) -> None:
    image = ImageOps.exif_transpose(image)
    if image.width * image.height > max_pixels:
        scale = math.sqrt(max_pixels / (image.width * image.height))
        image.thumbnail(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        alpha = image.getchannel("A")
        background.paste(image.convert("RGB"), mask=alpha)
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.save(target, format="JPEG", quality=90, optimize=True)
    os.chmod(target, 0o600)


def _pdf_page_count(payload: bytes, settings: Settings) -> int:
    try:
        document = PdfDocument(payload)
    except Exception as exc:
        raise OcrPermanentError("PDF 无法安全打开") from exc
    try:
        page_count = len(document)
        if page_count < 1:
            raise OcrPermanentError("PDF 没有可识别页面")
        if page_count > settings.ocr_max_pages:
            raise OcrPermanentError(f"PDF 超过 OCR 页数上限 {settings.ocr_max_pages}")
        return page_count
    finally:
        document.close()


def _render_pdf(
    payload: bytes,
    job_dir: Path,
    settings: Settings,
    *,
    page_start: int,
    page_end: int,
) -> list[Path]:
    try:
        document = PdfDocument(payload)
    except Exception as exc:
        raise OcrPermanentError("PDF 无法安全打开") from exc
    try:
        page_count = len(document)
        if page_start < 0 or page_end <= page_start or page_end > page_count:
            raise OcrPermanentError("PDF OCR 分页范围无效")
        images: list[Path] = []
        for index in range(page_start, page_end):
            page = document[index]
            try:
                width, height = page.get_size()
                base_pixels = max(1.0, width * height)
                scale = min(2.0, math.sqrt(settings.ocr_max_image_pixels / base_pixels))
                rendered = page.render(scale=max(0.5, scale))
                try:
                    image = rendered.to_pil()
                    target = job_dir / f"page-{index + 1:03d}.jpg"
                    _save_rgb_jpeg(image, target, max_pixels=settings.ocr_max_image_pixels)
                    images.append(target)
                finally:
                    rendered.close()
            finally:
                page.close()
        return images
    finally:
        document.close()


def _render_image(payload: bytes, job_dir: Path, settings: Settings) -> list[Path]:
    try:
        Image.MAX_IMAGE_PIXELS = settings.ocr_max_image_pixels
        with warnings.catch_warnings():
            warnings.simplefilter("error", DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                source.load()
                target = job_dir / "page-001.jpg"
                _save_rgb_jpeg(source, target, max_pixels=settings.ocr_max_image_pixels)
    except Exception as exc:
        raise OcrPermanentError("图片无法安全解码") from exc
    return [target]


def _prepare_job(
    snapshot: _SourceSnapshot,
    payload: bytes,
    settings: Settings,
    *,
    page_start: int = 0,
    page_end: int | None = None,
) -> Path:
    root = settings.ocr_jobs_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    job_dir = root / f"job-{snapshot.source_file_id}-{uuid.uuid4().hex}"
    job_dir.mkdir(mode=0o770)
    suffix = Path(snapshot.filename).suffix.casefold()
    try:
        if suffix == ".pdf":
            if page_end is None:
                page_end = _pdf_page_count(payload, settings)
            images = _render_pdf(
                payload,
                job_dir,
                settings,
                page_start=page_start,
                page_end=page_end,
            )
        else:
            images = _render_image(payload, job_dir, settings)
        request = {
            "contractVersion": OCR_CONTRACT_VERSION,
            "filename": snapshot.filename,
            "images": [image.name for image in images],
        }
        descriptor, raw_temporary = tempfile.mkstemp(prefix=".request-", dir=job_dir)
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(request, target, ensure_ascii=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, job_dir / "request.json")
        return job_dir
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


def _wait_for_result(job_dir: Path, settings: Settings) -> Path:
    deadline = time.monotonic() + settings.ocr_timeout_seconds + 15
    result_path = job_dir / "result.json"
    error_path = job_dir / "error.json"
    while time.monotonic() < deadline:
        if result_path.is_file():
            return result_path
        if error_path.is_file():
            try:
                detail = json.loads(error_path.read_text(encoding="utf-8")).get("error")
            except Exception:
                detail = None
            raise OcrJobError(str(detail or "Codex OCR 执行失败")[:500])
        time.sleep(settings.ocr_runner_poll_seconds)
    raise OcrJobError("等待 Codex OCR 结果超时")


def _persist_result(snapshot: _SourceSnapshot, outcome, settings: Settings) -> dict[str, object]:
    # Import lazily to avoid a module cycle: upload enqueueing imports this task
    # module only after the upload transaction has committed.
    from .imports import _category_lookup, _ingest_parse_outcome, _refresh_batch_status

    with get_session_factory()() as db:
        batch = db.scalar(
            select(ImportBatch)
            .where(ImportBatch.id == snapshot.batch_id)
            .options(selectinload(ImportBatch.candidates), selectinload(ImportBatch.files))
            .with_for_update()
        )
        if not batch or batch.status not in {"reviewing", "needs_ocr"}:
            return {"status": "skipped", "sourceFileId": snapshot.source_file_id}
        source_file = db.scalar(
            select(SourceFile).where(SourceFile.id == snapshot.source_file_id).with_for_update()
        )
        if not source_file or source_file.parse_status != "needs_ocr":
            return {"status": "skipped", "sourceFileId": snapshot.source_file_id}
        member = db.get(Member, batch.owner_id)
        if not member:
            raise OcrJobError("OCR 批次所有者不存在")
        _ingest_parse_outcome(
            db,
            member,
            batch,
            source_file,
            outcome,
            settings,
            _category_lookup(db),
            {},
        )
        db.flush()
        _refresh_batch_status(batch)
        record_event(
            db,
            None,
            "import_source.ocr_completed",
            "source_file",
            source_file.id,
            after={
                "provider": "codex-cli",
                "record_count": source_file.record_count,
                "candidate_count": source_file.candidate_count,
                "rejected_count": source_file.rejected_count,
            },
        )
        db.commit()
        return {
            "status": "parsed",
            "sourceFileId": source_file.id,
            "candidateCount": source_file.candidate_count,
        }


def process_ocr_source_file(
    source_file_id: str,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or get_settings()
    if not settings.ocr_enabled:
        return {"status": "disabled", "sourceFileId": source_file_id}
    snapshot = _source_snapshot(source_file_id, settings)
    if snapshot is None:
        return {"status": "skipped", "sourceFileId": source_file_id}
    payload = decrypt_source_file(
        snapshot.encrypted_path,
        source_file_id=snapshot.source_file_id,
        settings=settings,
    )
    suffix = Path(snapshot.filename).suffix.casefold()
    if suffix == ".pdf":
        page_count = _pdf_page_count(payload, settings)
        ranges = [
            (start, min(start + settings.ocr_pages_per_job, page_count))
            for start in range(0, page_count, settings.ocr_pages_per_job)
        ]
    else:
        page_count = 1
        ranges = [(0, 1)]

    outcomes: list[ParseOutcome] = []
    total_rows = 0
    for page_start, page_end in ranges:
        job_dir = _prepare_job(
            snapshot,
            payload,
            settings,
            page_start=page_start,
            page_end=page_end,
        )
        try:
            result_path = _wait_for_result(job_dir, settings)
            outcome = parse_ocr_result(
                result_path,
                max_transactions=settings.ocr_max_transactions,
            )
            total_rows += len(outcome.rows)
            if total_rows > settings.ocr_max_transactions:
                raise OcrResultError("OCR 返回的交易总条数超过批次限制")
            outcomes.append(outcome)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    if len(outcomes) == 1:
        combined = outcomes[0]
    else:
        rows = [row for outcome in outcomes for row in outcome.rows]
        valid = sum(row.can_create_candidate for row in rows)
        rejected = len(rows) - valid
        warning_count = sum(outcome.warning_count for outcome in outcomes)
        message = (
            f"Codex OCR 分 {len(outcomes)} 批识别 {page_count} 页、{len(rows)} 条，"
            f"生成 {valid} 条候选"
        )
        if rejected:
            message += f"；{rejected} 条未通过后端校验"
        if warning_count:
            message += f"；模型报告 {warning_count} 项识别警告"
        combined = ParseOutcome(
            status="parsed",
            message=message,
            rows=rows,
            warning_count=warning_count,
        )
    return _persist_result(snapshot, combined, settings)


def _record_failure(source_file_id: str, message: str) -> None:
    with get_session_factory()() as db:
        batch_id = db.scalar(select(SourceFile.batch_id).where(SourceFile.id == source_file_id))
        if not batch_id:
            return
        batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update())
        if not batch or batch.status not in {"reviewing", "needs_ocr"}:
            return
        source_file = db.scalar(
            select(SourceFile).where(SourceFile.id == source_file_id).with_for_update()
        )
        if not source_file or source_file.parse_status != "needs_ocr":
            return
        source_file.parse_message = f"Codex OCR 暂未完成：{message[:430]}"[:500]
        record_event(
            db,
            None,
            "import_source.ocr_failed",
            "source_file",
            source_file.id,
            after={"provider": "codex-cli", "reason": "worker_error"},
        )
        db.commit()


@celery_app.task(
    bind=True,
    name="homedatabase.ocr_source_file",
    acks_late=True,
    max_retries=2,
)
def ocr_source_file(self, source_file_id: str) -> dict[str, object]:
    try:
        return process_ocr_source_file(source_file_id)
    except (OcrPermanentError, OcrResultError, ValueError) as exc:
        _record_failure(source_file_id, str(exc) or type(exc).__name__)
        return {"status": "failed", "sourceFileId": source_file_id}
    except (OcrJobError, OSError) as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=exc,
                countdown=30 * (self.request.retries + 1),
            ) from exc
        _record_failure(source_file_id, str(exc) or type(exc).__name__)
        return {"status": "failed", "sourceFileId": source_file_id}
