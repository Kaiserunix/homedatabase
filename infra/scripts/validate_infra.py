"""Validate HomeDatabase infra configs, env templates, systemd units, and example fixtures."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
EXAMPLES = ROOT / "examples"
WORKFLOWS = ROOT / ".github" / "workflows"

_INTERPOLATION_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::[-?]?[^}]*)?\}")


def _parse_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def validate_yaml_files(*, strict: bool) -> list[str]:
    errors: list[str] = []
    try:
        import yaml  # type: ignore
    except ImportError:
        if strict:
            errors.append("PyYAML is required with --strict to parse YAML files")
            return errors
        return []

    yaml_files = sorted([*INFRA.glob("compose*.yml"), *WORKFLOWS.glob("*.yml")])
    for path in yaml_files:
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Invalid YAML in {path.relative_to(ROOT)}: {exc}")
            continue
        if not isinstance(loaded, dict):
            errors.append(f"Expected YAML mapping at root of {path.relative_to(ROOT)}")
    return errors


def validate_compose_env_parity() -> list[str]:
    errors: list[str] = []
    pairs = [
        (INFRA / "compose.yml", INFRA / ".env.example"),
        (INFRA / "compose.tls.yml", INFRA / ".env.example"),
        (INFRA / "compose.domestic.yml", INFRA / ".env.domestic.example"),
        (INFRA / "compose.overseas-worker.yml", INFRA / "overseas.env.example"),
    ]
    for compose_path, env_path in pairs:
        if not compose_path.is_file() or not env_path.is_file():
            errors.append(f"Missing expected pair: {compose_path} / {env_path}")
            continue
        text = compose_path.read_text(encoding="utf-8")
        # Ignore escaped $$ variables used inside shell commands (e.g. $${POSTGRES_USER})
        scrubbed = text.replace("$$", "")
        referenced = set(_INTERPOLATION_RE.findall(scrubbed))
        documented = _parse_env_keys(env_path)
        missing = sorted(referenced - documented)
        if missing:
            errors.append(
                f"{compose_path.name} references variables missing from {env_path.name}: "
                + ", ".join(missing)
            )
    return errors


def validate_security_boundaries() -> list[str]:
    errors: list[str] = []
    for compose_path in sorted(INFRA.glob("compose*.yml")):
        text = compose_path.read_text(encoding="utf-8")
        if re.search(r"HOMEDATABASE_DEV_MEMBER_ID:\s*['\"]?[a-zA-Z0-9_-]+", text):
            errors.append(f"{compose_path.name} must leave HOMEDATABASE_DEV_MEMBER_ID empty")
        if "HOMEDATABASE_AUTO_CREATE_SCHEMA: \"true\"" in text:
            errors.append(f"{compose_path.name} must set HOMEDATABASE_AUTO_CREATE_SCHEMA: \"false\"")
        if "HOMEDATABASE_SEED_DEMO: \"true\"" in text:
            errors.append(f"{compose_path.name} must set HOMEDATABASE_SEED_DEMO: \"false\"")

    try:
        import yaml  # type: ignore
    except ImportError:
        return errors

    for compose_path in (INFRA / "compose.yml", INFRA / "compose.domestic.yml"):
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = data.get("services", {})
        runner = services.get("ocr-runner", {})
        runner_env = runner.get("environment", {})
        for forbidden in (
            "HOMEDATABASE_DATABASE_URL",
            "HOMEDATABASE_REDIS_URL",
            "HOMEDATABASE_SOURCE_ENCRYPTION_KEY",
            "HOMEDATABASE_BACKUP_ENCRYPTION_KEY",
        ):
            if forbidden in runner_env:
                errors.append(f"{compose_path.name}: ocr-runner must never receive {forbidden}")
        for svc_name in ("postgres", "redis", "api", "web", "ocr-dispatcher", "ocr-runner"):
            svc = services.get(svc_name, {})
            if "healthcheck" not in svc:
                errors.append(f"{compose_path.name}: service {svc_name} is missing a healthcheck")
    return errors


def validate_systemd_units() -> list[str]:
    errors: list[str] = []
    for unit in sorted((INFRA / "systemd").glob("*")):
        text = unit.read_text(encoding="utf-8")
        if "[Unit]" not in text:
            errors.append(f"{unit.name} missing [Unit]")
        if unit.suffix == ".service" and "[Service]" not in text:
            errors.append(f"{unit.name} missing [Service]")
        if unit.suffix == ".timer" and "[Timer]" not in text:
            errors.append(f"{unit.name} missing [Timer]")
    return errors


def validate_examples_static() -> list[str]:
    errors: list[str] = []
    required_files = [
        EXAMPLES / "demo.sqlite3",
        EXAMPLES / "schema.sql",
        EXAMPLES / "transactions.csv",
        EXAMPLES / "import-template.csv",
        EXAMPLES / "fixtures" / "wechat-pay-sample.csv",
        EXAMPLES / "fixtures" / "alipay-sample.csv",
        EXAMPLES / "fixtures" / "bank-statement-split-columns.csv",
        EXAMPLES / "fixtures" / "multi-currency-travel.csv",
        EXAMPLES / "fixtures" / "english-ledger.csv",
        EXAMPLES / "fixtures" / "ocr-candidate-sample.json",
    ]
    for path in required_files:
        if not path.is_file():
            errors.append(f"Missing expected example file: {path.relative_to(ROOT)}")

    for csv_path in sorted((EXAMPLES / "fixtures").glob("*.csv")):
        try:
            content = csv_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"{csv_path.name} is not UTF-8: {exc}")
            continue
        rows = list(csv.reader(content.splitlines()))
        if len(rows) < 2:
            errors.append(f"{csv_path.name} must contain header and sample data rows")

    ocr_json = EXAMPLES / "fixtures" / "ocr-candidate-sample.json"
    if ocr_json.is_file():
        try:
            payload = json.loads(ocr_json.read_text(encoding="utf-8"))
            if not isinstance(payload.get("transactions"), list) or not payload["transactions"]:
                errors.append("ocr-candidate-sample.json must contain non-empty transactions")
        except Exception as exc:
            errors.append(f"Invalid JSON in ocr-candidate-sample.json: {exc}")
    return errors


def validate_with_backend_parsers() -> list[str]:
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from homedatabase_api.services.import_parsers import parse_import_file
    from homedatabase_api.services.ocr_results import parse_ocr_result

    errors: list[str] = []
    expected_counts = {
        "transactions.csv": 4,
        "fixtures/wechat-pay-sample.csv": 4,
        "fixtures/alipay-sample.csv": 4,
        "fixtures/bank-statement-split-columns.csv": 4,
        "fixtures/multi-currency-travel.csv": 4,
        "fixtures/english-ledger.csv": 4,
    }
    for rel_path, expected in expected_counts.items():
        target = EXAMPLES / rel_path
        outcome = parse_import_file(
            target,
            target.name,
            max_rows=50_000,
            max_uncompressed_bytes=100 * 1024 * 1024,
        )
        if outcome.status != "parsed":
            errors.append(f"{rel_path}: expected parsed, got {outcome.status} ({outcome.message})")
        elif outcome.candidate_count != expected:
            errors.append(
                f"{rel_path}: expected {expected} valid candidates, got {outcome.candidate_count} "
                f"(rejected={outcome.rejected_count}, issues={[r.issues for r in outcome.rows]})"
            )

    ocr_outcome = parse_ocr_result(
        EXAMPLES / "fixtures" / "ocr-candidate-sample.json",
        max_transactions=5000,
    )
    if ocr_outcome.status != "parsed" or ocr_outcome.candidate_count != 3:
        errors.append(
            f"ocr-candidate-sample.json: expected 3 valid candidates, got {ocr_outcome.candidate_count}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Fail if PyYAML is missing")
    parser.add_argument(
        "--verify-backend-parsers",
        action="store_true",
        help="Also parse all example CSV/OCR fixtures through homedatabase_api parsers",
    )
    args = parser.parse_args(argv)

    errors: list[str] = []
    errors.extend(validate_yaml_files(strict=args.strict))
    errors.extend(validate_compose_env_parity())
    errors.extend(validate_security_boundaries())
    errors.extend(validate_systemd_units())
    errors.extend(validate_examples_static())
    if args.verify_backend_parsers:
        errors.extend(validate_with_backend_parsers())

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("Infra validation passed: YAML, Compose env parity, security checks, systemd, examples OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
