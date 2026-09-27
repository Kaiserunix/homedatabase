from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException, UploadFile, status

from ..config import Settings

_MAGIC = b"HDSRC1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_CHUNK_BYTES = 1024 * 1024
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(slots=True)
class StagedUpload:
    path: Path
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str


def sanitise_filename(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = _CONTROL_RE.sub("", value).strip().strip(".")
    if not value:
        value = "未命名文件"
    stem = Path(value).stem[:220].strip().strip(".") or "未命名文件"
    suffix = Path(value).suffix[:20].casefold()
    return f"{stem}{suffix}"[:240]


async def stage_upload(
    upload: UploadFile,
    directory: Path,
    *,
    max_bytes: int,
    remaining_total_bytes: int,
) -> StagedUpload:
    filename = sanitise_filename(upload.filename)
    mime_type = (upload.content_type or "application/octet-stream")[:120]
    digest = hashlib.sha256()
    size = 0
    handle, raw_path = tempfile.mkstemp(prefix="source-", suffix=".upload", dir=directory)
    path = Path(raw_path)
    try:
        with os.fdopen(handle, "wb") as target:
            while chunk := await upload.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"{filename} 超过单文件 {max_bytes // (1024 * 1024)} MiB 限制",
                    )
                if size > remaining_total_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="本次上传文件总大小超过限制",
                    )
                digest.update(chunk)
                target.write(chunk)
        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{filename} 是空文件",
            )
        return StagedUpload(
            path=path,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def encrypt_staged_file(
    staged_path: Path,
    *,
    source_file_id: str,
    settings: Settings,
) -> Path:
    archive_dir = settings.source_archive_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    key = _source_encryption_key(settings)
    nonce = os.urandom(_NONCE_BYTES)
    aad = f"homedatabase-source-file:{source_file_id}".encode()
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(aad)

    target = archive_dir / f"{source_file_id}.hdsrc"
    temporary = archive_dir / f".{source_file_id}.{uuid.uuid4().hex}.tmp"
    try:
        with staged_path.open("rb") as source, temporary.open("xb") as destination:
            destination.write(_MAGIC)
            destination.write(nonce)
            while chunk := source.read(_CHUNK_BYTES):
                destination.write(encryptor.update(chunk))
            destination.write(encryptor.finalize())
            destination.write(encryptor.tag)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def decrypt_source_file(
    encrypted_path: Path,
    *,
    source_file_id: str,
    settings: Settings,
) -> bytes:
    """Decrypt a retained source file and verify its AES-GCM authentication tag."""
    payload = encrypted_path.read_bytes()
    minimum = len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES
    if len(payload) < minimum or not payload.startswith(_MAGIC):
        raise ValueError("invalid encrypted source file")
    nonce_start = len(_MAGIC)
    nonce_end = nonce_start + _NONCE_BYTES
    nonce = payload[nonce_start:nonce_end]
    ciphertext = payload[nonce_end:-_TAG_BYTES]
    tag = payload[-_TAG_BYTES:]
    decryptor = Cipher(
        algorithms.AES(_source_encryption_key(settings)),
        modes.GCM(nonce, tag),
    ).decryptor()
    decryptor.authenticate_additional_data(f"homedatabase-source-file:{source_file_id}".encode())
    return decryptor.update(ciphertext) + decryptor.finalize()


def remove_encrypted_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _source_encryption_key(settings: Settings) -> bytes:
    configured = settings.source_encryption_key
    if configured:
        encoded = configured.removeprefix("base64:").strip()
        try:
            key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "HOMEDATABASE_SOURCE_ENCRYPTION_KEY must be URL-safe base64"
            ) from exc
        if len(key) != 32:
            raise RuntimeError("HOMEDATABASE_SOURCE_ENCRYPTION_KEY must decode to 32 bytes")
        return key
    if settings.environment == "production":
        raise RuntimeError("production source-file encryption key is missing")
    return _load_or_create_local_key(settings.source_archive_dir.resolve().parent)


def _load_or_create_local_key(directory: Path) -> bytes:
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / ".source-encryption-key"
    try:
        return _validate_key_file(key_path.read_bytes())
    except FileNotFoundError:
        pass
    key = os.urandom(32)
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _validate_key_file(key_path.read_bytes())
    with os.fdopen(descriptor, "wb") as target:
        target.write(key)
        target.flush()
        os.fsync(target.fileno())
    return key


def _validate_key_file(key: bytes) -> bytes:
    if len(key) != 32:
        raise RuntimeError("local source encryption key is invalid")
    return key
