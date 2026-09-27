from __future__ import annotations

import argparse
import json
from pathlib import Path

from homedatabase_api.main import app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the deterministic HomeDatabase OpenAPI file"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "openapi.yaml",
        help="destination path; JSON is emitted because JSON is valid YAML",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"OpenAPI written to {args.output.resolve()}")


if __name__ == "__main__":
    main()
