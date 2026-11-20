"""Generate and validate HomeDatabase self-hosting environment files."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
from pathlib import Path

INFRA_DIR = Path(__file__).resolve().parents[1]
_URL_SAFE_SECRET_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _generate_encryption_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _generate_url_safe_password() -> str:
    return secrets.token_urlsafe(32)


def _decode_32_byte_key(value: str, name: str) -> bytes:
    if not value or "CHANGE_ME" in value:
        raise ValueError(f"{name} is unset or still uses a CHANGE_ME placeholder")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise ValueError(f"{name} is not valid URL-safe base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes (got {len(decoded)})")
    return decoded


def check_env_file(path: Path, *, mode: str | None = None) -> list[str]:
    if not path.is_file():
        return [f"Env file does not exist: {path}"]
    env = parse_env_file(path)
    errors: list[str] = []

    if mode == "overseas" or (
        mode is None
        and "HOMEDATABASE_REDIS_URL" in env
        and "HOMEDATABASE_DB_PASSWORD" not in env
    ):
        redis_url = env.get("HOMEDATABASE_REDIS_URL", "")
        if not redis_url or "CHANGE_ME" in redis_url:
            errors.append("HOMEDATABASE_REDIS_URL is unset or contains CHANGE_ME")
        elif not redis_url.startswith(("redis://", "rediss://")):
            errors.append("HOMEDATABASE_REDIS_URL must start with redis:// or rediss://")
        for forbidden in (
            "HOMEDATABASE_DATABASE_URL",
            "HOMEDATABASE_DB_PASSWORD",
            "HOMEDATABASE_SOURCE_ENCRYPTION_KEY",
            "HOMEDATABASE_BACKUP_ENCRYPTION_KEY",
        ):
            if env.get(forbidden):
                errors.append(f"Overseas worker env must never set {forbidden}")
        return errors

    for key in ("HOMEDATABASE_DB_PASSWORD", "HOMEDATABASE_REDIS_PASSWORD"):
        value = env.get(key, "")
        if not value or "CHANGE_ME" in value:
            errors.append(f"{key} is unset or still uses a CHANGE_ME placeholder")
        elif not _URL_SAFE_SECRET_RE.fullmatch(value):
            errors.append(f"{key} must contain only URL-safe characters [A-Za-z0-9._~-]")

    source_raw = env.get("HOMEDATABASE_SOURCE_ENCRYPTION_KEY", "")
    backup_raw = env.get("HOMEDATABASE_BACKUP_ENCRYPTION_KEY", "")
    source_bytes: bytes | None = None
    backup_bytes: bytes | None = None
    try:
        source_bytes = _decode_32_byte_key(source_raw, "HOMEDATABASE_SOURCE_ENCRYPTION_KEY")
    except ValueError as exc:
        errors.append(str(exc))
    try:
        backup_bytes = _decode_32_byte_key(backup_raw, "HOMEDATABASE_BACKUP_ENCRYPTION_KEY")
    except ValueError as exc:
        errors.append(str(exc))
    if source_bytes is not None and backup_bytes is not None and source_bytes == backup_bytes:
        errors.append(
            "HOMEDATABASE_SOURCE_ENCRYPTION_KEY and HOMEDATABASE_BACKUP_ENCRYPTION_KEY must differ"
        )

    if env.get("HOMEDATABASE_DEV_MEMBER_ID", ""):
        errors.append("HOMEDATABASE_DEV_MEMBER_ID must be empty in Docker deployments")

    environment = env.get("HOMEDATABASE_ENVIRONMENT", "production" if mode == "domestic" else "local")
    if environment == "production" or mode in {"production", "domestic"}:
        rp_id = env.get("HOMEDATABASE_WEBAUTHN_RP_ID") or env.get("HOMEDATABASE_PUBLIC_HOST", "")
        if not rp_id or rp_id in {"localhost", "127.0.0.1", "finance.example.com"}:
            errors.append(
                "Production mode requires a real non-example domain for "
                "HOMEDATABASE_PUBLIC_HOST / HOMEDATABASE_WEBAUTHN_RP_ID"
            )
        if mode != "domestic":
            if env.get("HOMEDATABASE_SESSION_COOKIE_SECURE", "").lower() != "true":
                errors.append("Production mode requires HOMEDATABASE_SESSION_COOKIE_SECURE=true")
            origins_raw = env.get("HOMEDATABASE_WEBAUTHN_ORIGINS", "[]")
            try:
                origins = json.loads(origins_raw)
                if not isinstance(origins, list) or not origins or any(
                    not isinstance(item, str) or not item.startswith("https://") for item in origins
                ):
                    errors.append("Production mode requires HTTPS HOMEDATABASE_WEBAUTHN_ORIGINS")
            except json.JSONDecodeError:
                errors.append("HOMEDATABASE_WEBAUTHN_ORIGINS must be a JSON array")

    return errors


def _render_from_template(template: Path, updates: dict[str, str]) -> str:
    rendered_lines: list[str] = []
    seen: set[str] = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        if line and not line.lstrip().startswith("#") and "=" in line:
            key, current = line.split("=", 1)
            key = key.strip()
            if key in updates:
                seen.add(key)
                rendered_lines.append(f"{key}={updates[key]}")
                continue
            rendered_lines.append(f"{key}={current}")
        else:
            rendered_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            rendered_lines.append(f"{key}={value}")
    return "\n".join(rendered_lines) + "\n"


def generate_env(args: argparse.Namespace) -> Path:
    mode: str = args.mode or "local"
    if args.output:
        output = args.output.resolve()
    elif mode == "domestic":
        output = INFRA_DIR / ".env.domestic"
    elif mode == "overseas":
        output = INFRA_DIR / ".env.overseas"
    else:
        output = INFRA_DIR / ".env"

    if output.exists() and not args.force:
        raise SystemExit(
            f"Refusing to overwrite existing env file: {output} (pass --force to replace)"
        )

    if mode == "overseas":
        template = INFRA_DIR / "overseas.env.example"
        updates: dict[str, str] = {}
        if args.redis_url:
            updates["HOMEDATABASE_REDIS_URL"] = args.redis_url
        content = _render_from_template(template, updates)
    else:
        updates = {
            "HOMEDATABASE_DB_PASSWORD": _generate_url_safe_password(),
            "HOMEDATABASE_REDIS_PASSWORD": _generate_url_safe_password(),
            "HOMEDATABASE_SOURCE_ENCRYPTION_KEY": _generate_encryption_key(),
            "HOMEDATABASE_BACKUP_ENCRYPTION_KEY": _generate_encryption_key(),
        }
        if mode == "domestic":
            if not args.host:
                raise SystemExit("--host is required for --mode domestic (e.g. finance.example.com)")
            template = INFRA_DIR / ".env.domestic.example"
            updates["HOMEDATABASE_PUBLIC_HOST"] = args.host
            if args.port:
                updates["HOMEDATABASE_HTTP_PORT"] = str(args.port)
        elif mode == "production":
            if not args.host:
                raise SystemExit("--host is required for --mode production (e.g. finance.example.com)")
            template = INFRA_DIR / ".env.example"
            http_port = str(args.port or 80)
            updates.update(
                {
                    "HOMEDATABASE_ENVIRONMENT": "production",
                    "HOMEDATABASE_SESSION_COOKIE_SECURE": "true",
                    "HOMEDATABASE_CADDY_SITE": args.host,
                    "HOMEDATABASE_PUBLIC_HOST": args.host,
                    "HOMEDATABASE_HTTP_BIND": args.bind or "0.0.0.0",
                    "HOMEDATABASE_HTTP_PORT": http_port,
                    "HOMEDATABASE_WEBAUTHN_RP_ID": args.host,
                    "HOMEDATABASE_WEBAUTHN_ORIGINS": json.dumps([f"https://{args.host}"]),
                    "HOMEDATABASE_ALLOWED_HOSTS": json.dumps([args.host]),
                }
            )
        else:
            template = INFRA_DIR / ".env.example"
            http_port = str(args.port or 8080)
            origins = [f"http://localhost:{http_port}", f"http://127.0.0.1:{http_port}"]
            hosts = ["localhost", "127.0.0.1"]
            bind_ip = args.bind or "127.0.0.1"
            if args.lan_ip:
                bind_ip = args.bind or "0.0.0.0"
                origins.append(f"http://{args.lan_ip}:{http_port}")
                hosts.append(args.lan_ip)
            updates.update(
                {
                    "HOMEDATABASE_ENVIRONMENT": "local",
                    "HOMEDATABASE_SESSION_COOKIE_SECURE": "false",
                    "HOMEDATABASE_CADDY_SITE": ":80",
                    "HOMEDATABASE_HTTP_BIND": bind_ip,
                    "HOMEDATABASE_HTTP_PORT": http_port,
                    "HOMEDATABASE_WEBAUTHN_RP_ID": "localhost",
                    "HOMEDATABASE_WEBAUTHN_ORIGINS": json.dumps(origins),
                    "HOMEDATABASE_ALLOWED_HOSTS": json.dumps(hosts),
                }
            )
        content = _render_from_template(template, updates)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    if os.name != "nt":
        output.chmod(0o600)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("local", "production", "domestic", "overseas"),
        default=None,
        help="Deployment profile for generated/checked env file (default: local on generate)",
    )
    parser.add_argument("--output", type=Path, help="Destination .env path")
    parser.add_argument("--host", help="Public HTTPS hostname for production/domestic modes")
    parser.add_argument("--port", type=int, help="Published HTTP port (default 8080 local, 80 prod)")
    parser.add_argument("--bind", help="Host IP bind address for web HTTP port")
    parser.add_argument("--lan-ip", help="Optional LAN/Tailscale IP to allow in local mode")
    parser.add_argument("--redis-url", help="Private tunnel Redis URL for overseas mode")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing env file")
    parser.add_argument("--check", type=Path, help="Validate an existing env file and exit")
    args = parser.parse_args(argv)

    if args.check:
        errors = check_env_file(args.check.resolve(), mode=args.mode)
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print(f"Env file valid: {args.check}")
        return 0

    generated = generate_env(args)
    print(f"Generated {generated} (mode={args.mode or 'local'}); secrets were not printed to stdout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
