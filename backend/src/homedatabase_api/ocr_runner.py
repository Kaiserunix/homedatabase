from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .ocr_contract import OCR_CONTRACT_VERSION, OCR_OUTPUT_SCHEMA, OCR_PROMPT

_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_KILL_SIGNAL = getattr(signal, "SIGKILL", 9)
_STOP = False


class RunnerRequestError(ValueError):
    pass


def _stop(_signum: int, _frame: object) -> None:
    global _STOP
    _STOP = True


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _load_request(path: Path) -> tuple[str, list[Path]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerRequestError("invalid request JSON") from exc
    if not isinstance(payload, dict) or payload.get("contractVersion") != OCR_CONTRACT_VERSION:
        raise RunnerRequestError("unsupported OCR contract")
    filename = payload.get("filename")
    names = payload.get("images")
    if not isinstance(filename, str) or not filename or len(filename) > 300:
        raise RunnerRequestError("invalid source filename")
    if not isinstance(names, list) or not 1 <= len(names) <= 100:
        raise RunnerRequestError("invalid image list")
    job_dir = path.parent.resolve()
    images: list[Path] = []
    for name in names:
        if not isinstance(name, str) or name != Path(name).name:
            raise RunnerRequestError("invalid image path")
        image = (job_dir / name).resolve()
        if image.parent != job_dir or image.suffix.casefold() not in _ALLOWED_IMAGE_SUFFIXES:
            raise RunnerRequestError("unsupported image path")
        if not image.is_file() or image.stat().st_size > 30 * 1024 * 1024:
            raise RunnerRequestError("missing or oversized image")
        images.append(image)
    return filename, images


def _codex_environment() -> dict[str, str]:
    allowed = {"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TZ"}
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    environment.setdefault("HOME", "/home/homedatabase")
    environment.setdefault("CODEX_HOME", f"{environment['HOME']}/.codex")
    environment.setdefault("LANG", "C.UTF-8")
    environment["NO_COLOR"] = "1"
    environment["TERM"] = "dumb"
    return environment


def _write_schema() -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="homedatabase-ocr-schema-", suffix=".json")
    path = Path(raw_path)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(OCR_OUTPUT_SCHEMA, target, ensure_ascii=False)
    return path


def _command(job_dir: Path, images: list[Path], schema_path: Path, output_path: Path) -> list[str]:
    command = [
        "codex",
        "--disable",
        "shell_tool",
        "--disable",
        "apps",
        "--disable",
        "browser_use",
        "--disable",
        "code_mode_host",
        "--disable",
        "computer_use",
        "--disable",
        "goals",
        "--disable",
        "image_generation",
        "--disable",
        "memories",
        "--disable",
        "multi_agent",
        "--disable",
        "workspace_dependencies",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--cd",
        str(job_dir),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    model = os.environ.get("HOMEDATABASE_OCR_CODEX_MODEL", "").strip()
    if model:
        command.extend(["--model", model])
    for image in images:
        command.extend(["--image", str(image)])
    # Passing a multiline prompt on the command line is fragile through npm's
    # Windows shim and exposes it in process listings. Codex accepts `-` as a
    # request to read the fixed prompt from stdin.
    command.append("-")
    return command


def _run_codex(
    command: list[str],
    *,
    job_dir: Path,
    environment: dict[str, str],
    prompt: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    # npm launches the native Codex binary as a child process.  Killing only
    # npm on timeout leaves that binary orphaned and a retry can then run two
    # credential-consuming OCR jobs concurrently.  The runner is Linux-only,
    # so start a dedicated process group and terminate the whole group.
    process = subprocess.Popen(
        command,
        cwd=job_dir,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, _KILL_SIGNAL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def process_request(request_path: Path, *, schema_path: Path, timeout_seconds: int) -> None:
    job_dir = request_path.parent.resolve()
    running_path = job_dir / "running.json"
    try:
        os.replace(request_path, running_path)
    except FileNotFoundError:
        return
    output_path = job_dir / "codex-output.json"
    try:
        _filename, images = _load_request(running_path)
        image_names = "、".join(image.name for image in images)
        prompt = (
            f"{OCR_PROMPT}\n\n本次附件文件名依次为：{image_names}。"
            "page-NNN 中的 NNN 是整份原文件的真实页码；sourceRef 必须沿用该真实页码，"
            "不要按本次附件顺序从第1页重新编号。"
        )
        completed = _run_codex(
            _command(job_dir, images, schema_path, output_path),
            job_dir=job_dir,
            environment=_codex_environment(),
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )
        if completed.returncode != 0:
            stderr = _CONTROL_RE.sub("", completed.stderr[-4000:]).casefold()
            if "login" in stderr or "authentication" in stderr or "unauthorized" in stderr:
                raise RuntimeError("Codex 登录凭证不可用")
            raise RuntimeError(f"Codex CLI 返回失败（exit {completed.returncode}）")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("codex did not write a result")
        # The trusted dispatcher performs the authoritative schema and money
        # validation.  The runner only publishes an atomic completion signal.
        os.replace(output_path, job_dir / "result.json")
    except subprocess.TimeoutExpired:
        _atomic_json(job_dir / "error.json", {"error": "Codex OCR 处理超时"})
    except Exception as exc:
        _atomic_json(
            job_dir / "error.json",
            {"error": _CONTROL_RE.sub("", str(exc))[:500] or type(exc).__name__},
        )


def _cleanup_stale(root: Path, *, stale_seconds: int) -> None:
    cutoff = time.time() - stale_seconds
    for candidate in root.iterdir():
        try:
            if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate)
        except FileNotFoundError:
            continue


def run() -> None:
    root = Path(os.environ.get("HOMEDATABASE_OCR_JOBS_DIR", "/var/lib/homedatabase/ocr-jobs"))
    root.mkdir(parents=True, exist_ok=True)
    poll_seconds = max(0.1, float(os.environ.get("HOMEDATABASE_OCR_RUNNER_POLL_SECONDS", "0.5")))
    timeout_seconds = max(30, int(os.environ.get("HOMEDATABASE_OCR_TIMEOUT_SECONDS", "300")))
    stale_seconds = max(600, int(os.environ.get("HOMEDATABASE_OCR_JOB_STALE_SECONDS", "3600")))
    schema_path = _write_schema()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    last_cleanup = 0.0
    try:
        while not _STOP:
            requests = sorted(root.glob("*/request.json"))
            if requests:
                process_request(
                    requests[0],
                    schema_path=schema_path,
                    timeout_seconds=timeout_seconds,
                )
                continue
            if time.monotonic() - last_cleanup > 60:
                _cleanup_stale(root, stale_seconds=stale_seconds)
                last_cleanup = time.monotonic()
            time.sleep(poll_seconds)
    finally:
        schema_path.unlink(missing_ok=True)


if __name__ == "__main__":
    run()
