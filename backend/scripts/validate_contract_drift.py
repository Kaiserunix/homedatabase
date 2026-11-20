"""Validate OpenAPI schema conformance and cross-stack contract drift."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from homedatabase_api.main import app
from homedatabase_api.money import MAX_SAFE_MINOR

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(ref: str, root_spec: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Unsupported external $ref: {ref}")
    target: Any = root_spec
    for part in ref[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            raise ValueError(f"Broken $ref: {ref}")
        target = target[part]
    if not isinstance(target, dict):
        raise ValueError(f"Non-object $ref target: {ref}")
    return target


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _matches_format(value: str, fmt: str) -> bool:
    if fmt == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if fmt == "date-time":
        if "T" not in value and " " not in value:
            return False
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            datetime.fromisoformat(normalized)
        except ValueError:
            return False
        return True
    return True


def validate_against_schema(
    value: Any,
    schema: dict[str, Any],
    root_spec: dict[str, Any],
    *,
    allow_extra_properties: bool = False,
    path: str = "$",
) -> list[str]:
    """Validate a Python JSON value against an OpenAPI 3.1 / JSON Schema node."""
    if not schema:
        return []

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root_spec)
        return validate_against_schema(
            value,
            target,
            root_spec,
            allow_extra_properties=allow_extra_properties,
            path=path,
        )

    if "anyOf" in schema:
        branch_errors: list[list[str]] = []
        for branch in schema["anyOf"]:
            errs = validate_against_schema(
                value,
                branch,
                root_spec,
                allow_extra_properties=allow_extra_properties,
                path=path,
            )
            if not errs:
                return []
            branch_errors.append(errs)
        joined = " | ".join(errs[0] for errs in branch_errors if errs)
        return [f"{path}: failed anyOf ({joined})"]

    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            if not validate_against_schema(
                value,
                branch,
                root_spec,
                allow_extra_properties=allow_extra_properties,
                path=path,
            ):
                matches += 1
        if matches != 1:
            return [f"{path}: expected exactly 1 oneOf match, got {matches}"]
        return []

    if "allOf" in schema:
        errors: list[str] = []
        for branch in schema["allOf"]:
            errors.extend(
                validate_against_schema(
                    value,
                    branch,
                    root_spec,
                    allow_extra_properties=allow_extra_properties,
                    path=path,
                )
            )
        return errors

    if "const" in schema and value != schema["const"]:
        return [f"{path}: expected const {schema['const']!r}, got {value!r}"]

    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: value {value!r} not in enum {schema['enum']!r}"]

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        type_ok = any(
            (t == "null" and value is None)
            or (t == "boolean" and isinstance(value, bool))
            or (t == "integer" and _is_int(value))
            or (t == "number" and _is_number(value))
            or (t == "string" and isinstance(value, str))
            or (t == "array" and isinstance(value, list))
            or (t == "object" and isinstance(value, dict))
            for t in allowed_types
        )
        if not type_ok:
            return [f"{path}: expected type {expected_type!r}, got {type(value).__name__}"]

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return [f"{path}: string length {len(value)} < minLength {schema['minLength']}"]
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return [f"{path}: string length {len(value)} > maxLength {schema['maxLength']}"]
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            return [f"{path}: string {value!r} does not match pattern {schema['pattern']!r}"]
        if "format" in schema and not _matches_format(value, schema["format"]):
            return [f"{path}: string {value!r} does not match format {schema['format']!r}"]

    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            return [f"{path}: {value} < minimum {schema['minimum']}"]
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return [f"{path}: {value} <= exclusiveMinimum {schema['exclusiveMinimum']}"]
        if "maximum" in schema and value > schema["maximum"]:
            return [f"{path}: {value} > maximum {schema['maximum']}"]
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return [f"{path}: {value} >= exclusiveMaximum {schema['exclusiveMaximum']}"]

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return [f"{path}: array length {len(value)} < minItems {schema['minItems']}"]
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return [f"{path}: array length {len(value)} > maxItems {schema['maxItems']}"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            errors: list[str] = []
            for idx, item in enumerate(value):
                errors.extend(
                    validate_against_schema(
                        item,
                        item_schema,
                        root_spec,
                        allow_extra_properties=allow_extra_properties,
                        path=f"{path}[{idx}]",
                    )
                )
            return errors

    if isinstance(value, dict):
        errors = []
        has_declared_props = "properties" in schema
        props = schema.get("properties", {})
        for req_key in schema.get("required", []):
            if req_key not in value:
                errors.append(f"{path}.{req_key}: missing required property")
        add_props = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                errors.extend(
                    validate_against_schema(
                        item,
                        props[key],
                        root_spec,
                        allow_extra_properties=allow_extra_properties,
                        path=f"{path}.{key}",
                    )
                )
            elif add_props is True or (
                add_props is not False and (allow_extra_properties or not has_declared_props)
            ):
                continue
            elif isinstance(add_props, dict):
                errors.extend(
                    validate_against_schema(
                        item,
                        add_props,
                        root_spec,
                        allow_extra_properties=allow_extra_properties,
                        path=f"{path}.{key}",
                    )
                )
            else:
                errors.append(f"{path}.{key}: unexpected property not declared in schema")
        return errors

    return []


def _strip_decimal_pattern_difference(schema: Any) -> Any:
    """Normalize Pydantic 2.13 vs 2.14+ Decimal string pattern omission in anyOf."""
    if isinstance(schema, list):
        return [_strip_decimal_pattern_difference(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, val in schema.items():
        if key == "anyOf" and isinstance(val, list):
            normalized_branches = []
            for branch in val:
                if (
                    isinstance(branch, dict)
                    and branch.get("type") == "string"
                    and set(branch.keys()) == {"type", "pattern"}
                    and str(branch.get("pattern", "")).startswith("^(?!^[-+.]*$)")
                ):
                    normalized_branches.append({"type": "string"})
                else:
                    normalized_branches.append(_strip_decimal_pattern_difference(branch))
            out[key] = normalized_branches
        else:
            out[key] = _strip_decimal_pattern_difference(val)
    return out


def validate_live_openapi_parity(
    live_spec: dict[str, Any],
    committed_spec: dict[str, Any],
) -> list[str]:
    """Compare live FastAPI app.openapi() against committed openapi.yaml."""
    errors: list[str] = []
    for top_key in ("openapi", "info"):
        if live_spec.get(top_key) != committed_spec.get(top_key):
            errors.append(f"OpenAPI {top_key!r} mismatch: {live_spec.get(top_key)!r}")

    live_schemes = live_spec.get("components", {}).get("securitySchemes", {})
    comm_schemes = committed_spec.get("components", {}).get("securitySchemes", {})
    if live_schemes != comm_schemes:
        errors.append("components.securitySchemes drifted between live app and openapi.yaml")

    live_paths = live_spec.get("paths", {})
    comm_paths = committed_spec.get("paths", {})
    if set(live_paths) != set(comm_paths):
        missing = sorted(set(live_paths) - set(comm_paths))
        extra = sorted(set(comm_paths) - set(live_paths))
        errors.append(f"OpenAPI paths drifted: missing={missing}, extra={extra}")
    else:
        for path in sorted(live_paths):
            live_methods = {m for m in live_paths[path] if m in HTTP_METHODS}
            comm_methods = {m for m in comm_paths[path] if m in HTTP_METHODS}
            if live_methods != comm_methods:
                errors.append(
                    f"Path {path} methods drifted: live={sorted(live_methods)} vs "
                    f"committed={sorted(comm_methods)}"
                )
                continue
            for method in sorted(live_methods):
                l_op = _strip_decimal_pattern_difference(live_paths[path][method])
                c_op = _strip_decimal_pattern_difference(comm_paths[path][method])
                if l_op != c_op:
                    errors.append(f"Operation {method.upper()} {path} differs from openapi.yaml")

    live_schemas = live_spec.get("components", {}).get("schemas", {})
    comm_schemas = committed_spec.get("components", {}).get("schemas", {})
    if set(live_schemas) != set(comm_schemas):
        missing = sorted(set(live_schemas) - set(comm_schemas))
        extra = sorted(set(comm_schemas) - set(live_schemas))
        errors.append(f"components.schemas drifted: missing={missing}, extra={extra}")
    else:
        for name in sorted(live_schemas):
            l_schema = _strip_decimal_pattern_difference(live_schemas[name])
            c_schema = _strip_decimal_pattern_difference(comm_schemas[name])
            if l_schema != c_schema:
                errors.append(f"Schema {name} differs between live app and openapi.yaml")

    return errors


def validate_openapi_invariants(spec: dict[str, Any]) -> list[str]:
    """Verify security, reference integrity, money bounds, and currency patterns."""
    errors: list[str] = []
    schemas = spec.get("components", {}).get("schemas", {})

    def walk_refs(node: Any, where: str) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                try:
                    _resolve_ref(node["$ref"], spec)
                except ValueError as exc:
                    errors.append(f"{where}: {exc}")
            for val in node.values():
                walk_refs(val, where)
        elif isinstance(node, list):
            for item in node:
                walk_refs(item, where)

    walk_refs(spec.get("paths", {}), "paths")
    walk_refs(schemas, "components.schemas")

    for path, methods in sorted(spec.get("paths", {}).items()):
        for method, op in sorted(methods.items()):
            if method not in HTTP_METHODS or not isinstance(op, dict):
                continue
            param_names = {p["name"].casefold() for p in op.get("parameters", [])}
            if "x-dev-member-id" in param_names or "authorization" in param_names:
                errors.append(f"{method.upper()} {path}: exposes forbidden auth header param")
            if op.get("security"):
                schemes = {next(iter(item)) for item in op["security"]}
                if schemes != {"SessionCookie", "BearerToken"}:
                    errors.append(f"{method.upper()} {path}: unexpected security schemes {schemes}")
                responses = op.get("responses", {})
                if "401" not in responses or "403" not in responses:
                    errors.append(f"{method.upper()} {path}: missing 401/403 responses")

    for schema_name, schema in sorted(schemas.items()):
        for prop_name, prop_schema in schema.get("properties", {}).items():
            if prop_name.endswith("Cents"):
                int_opt = next(
                    (
                        opt
                        for opt in prop_schema.get("anyOf", [prop_schema])
                        if opt.get("type") == "integer"
                    ),
                    None,
                )
                if not int_opt or int_opt.get("maximum") != MAX_SAFE_MINOR:
                    errors.append(f"{schema_name}.{prop_name}: missing MAX_SAFE_MINOR upper bound")
            if prop_name in {"currency", "baseCurrency", "accountCurrency"}:
                str_opt = next(
                    (
                        opt
                        for opt in prop_schema.get("anyOf", [prop_schema])
                        if opt.get("type") == "string"
                    ),
                    None,
                )
                if not str_opt or str_opt.get("pattern") != "^[A-Z]{3}$":
                    errors.append(f"{schema_name}.{prop_name}: missing ISO 4217 currency pattern")

    return errors


def _parse_schema_gen_paths(ts_text: str) -> dict[str, set[str]]:
    match = re.search(r"export interface paths \{(.*?)\r?\n\}", ts_text, re.S)
    if not match:
        return {}
    body = match.group(1)
    result: dict[str, set[str]] = {}
    path_matches = list(re.finditer(r'^    "([^"]+)": \{', body, re.M))
    for idx, m in enumerate(path_matches):
        start = m.end()
        end = path_matches[idx + 1].start() if idx + 1 < len(path_matches) else len(body)
        block = body[start:end]
        methods: set[str] = set()
        for method in HTTP_METHODS:
            if re.search(rf"^        {method}: operations\[", block, re.M):
                methods.add(method)
        result[m.group(1)] = methods
    return result


def _parse_schema_gen_schemas(ts_text: str) -> dict[str, set[str]]:
    match = re.search(r"    schemas: \{(.*?)\r?\n    \};", ts_text, re.S)
    if not match:
        return {}
    body = match.group(1)
    result: dict[str, set[str]] = {}
    schema_matches = list(re.finditer(r"^        ([A-Za-z0-9_]+): \{", body, re.M))
    for idx, m in enumerate(schema_matches):
        start = m.end()
        end = schema_matches[idx + 1].start() if idx + 1 < len(schema_matches) else len(body)
        block = body[start:end]
        props = set(re.findall(r"^            ([A-Za-z0-9_]+)\??:", block, re.M))
        result[m.group(1)] = props
    return result


def _parse_resources_api_calls(resources_text: str) -> list[tuple[str, str, str]]:
    """Extract (exported_fn_name, HTTP_METHOD, normalized_path) for apiFetch calls."""
    fn_matches = list(re.finditer(r"^export function ([A-Za-z0-9_]+)\(", resources_text, re.M))
    calls: list[tuple[str, str, str]] = []
    api_pattern = re.compile(
        r'apiFetch(?:<[^>]+>)?\(\s*([`"\'])([^`"\']+)\1'
        r"(?:\s*,\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\})?",
        re.S,
    )
    for idx, fn_m in enumerate(fn_matches):
        fn_name = fn_m.group(1)
        start = fn_m.end()
        end = fn_matches[idx + 1].start() if idx + 1 < len(fn_matches) else len(resources_text)
        fn_body = resources_text[start:end]
        for call_m in api_pattern.finditer(fn_body):
            raw_path = call_m.group(2)
            opts = call_m.group(3) or ""
            method_m = re.search(r'method:\s*"([A-Z]+)"', opts)
            method = method_m.group(1) if method_m else "GET"
            norm_path = re.sub(r"\$\{[^}]+\}", "{}", raw_path)
            calls.append((fn_name, method, norm_path))
    return calls


def validate_frontend_contract_drift(
    repo_root: Path,
    spec: dict[str, Any],
    endpoints_manifest: dict[str, Any],
) -> list[str]:
    """Verify frontend schema.gen.ts, types.ts, resources.ts, and endpoints.json match OpenAPI."""
    errors: list[str] = []
    schema_gen_path = repo_root / "frontend/src/api/schema.gen.ts"
    types_path = repo_root / "frontend/src/data/types.ts"
    resources_path = repo_root / "frontend/src/api/resources.ts"

    schema_gen_text = schema_gen_path.read_text(encoding="utf-8")
    types_text = types_path.read_text(encoding="utf-8")
    resources_text = resources_path.read_text(encoding="utf-8")

    # 1. schema.gen.ts paths & methods vs openapi.yaml
    gen_paths = _parse_schema_gen_paths(schema_gen_text)
    openapi_paths = {
        path: {m for m in methods if m in HTTP_METHODS}
        for path, methods in spec.get("paths", {}).items()
    }
    if set(gen_paths) != set(openapi_paths):
        errors.append(
            f"schema.gen.ts paths drifted: missing={sorted(set(openapi_paths) - set(gen_paths))}, "
            f"extra={sorted(set(gen_paths) - set(openapi_paths))}"
        )
    else:
        for path in sorted(openapi_paths):
            if gen_paths[path] != openapi_paths[path]:
                errors.append(
                    f"schema.gen.ts methods drifted for {path}: "
                    f"{sorted(gen_paths[path])} vs {sorted(openapi_paths[path])}"
                )

    # 2. schema.gen.ts schemas & properties vs openapi.yaml
    gen_schemas = _parse_schema_gen_schemas(schema_gen_text)
    openapi_schemas = spec.get("components", {}).get("schemas", {})
    if set(gen_schemas) != set(openapi_schemas):
        errors.append(
            f"schema.gen.ts schemas drifted: "
            f"missing={sorted(set(openapi_schemas) - set(gen_schemas))}, "
            f"extra={sorted(set(gen_schemas) - set(openapi_schemas))}"
        )
    else:
        for schema_name, schema_def in sorted(openapi_schemas.items()):
            expected_props = set(schema_def.get("properties", {}).keys())
            if gen_schemas[schema_name] != expected_props:
                errors.append(
                    f"schema.gen.ts properties drifted for {schema_name}: "
                    f"missing={sorted(expected_props - gen_schemas[schema_name])}, "
                    f"extra={sorted(gen_schemas[schema_name] - expected_props)}"
                )

    # 3. types.ts S["..."] references exist in openapi.yaml
    for referenced in re.findall(r'S\["([^"]+)"\]', types_text):
        if referenced not in openapi_schemas:
            errors.append(f"frontend/src/data/types.ts references missing schema {referenced!r}")

    # 4. endpoints.json vs openapi.yaml
    manifest_entries = endpoints_manifest.get("endpoints", [])
    manifest_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in manifest_entries:
        key = (entry["method"].upper(), entry["openApiPath"])
        if key in manifest_by_key:
            errors.append(f"endpoints.json contains duplicate entry {key}")
        manifest_by_key[key] = entry

    openapi_op_keys = {
        (m.upper(), p)
        for p, methods in spec.get("paths", {}).items()
        for m in methods
        if m in HTTP_METHODS
    }
    if set(manifest_by_key) != openapi_op_keys:
        errors.append(
            f"endpoints.json coverage drifted: "
            f"missing={sorted(openapi_op_keys - set(manifest_by_key))}, "
            f"extra={sorted(set(manifest_by_key) - openapi_op_keys)}"
        )

    for (method, path), entry in sorted(manifest_by_key.items()):
        op = spec.get("paths", {}).get(path, {}).get(method.lower())
        if not op:
            continue
        if entry["clientPath"] != path.removeprefix("/api"):
            errors.append(f"endpoints.json {method} {path}: clientPath {entry['clientPath']!r}")
        expected_status = int(next(k for k in ("200", "201", "204") if k in op["responses"]))
        if entry["successStatus"] != expected_status:
            errors.append(
                f"endpoints.json {method} {path}: successStatus {entry['successStatus']} != "
                f"{expected_status}"
            )
        op_query = [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"]
        if entry["queryParams"] != op_query:
            errors.append(
                f"endpoints.json {method} {path}: queryParams {entry['queryParams']} != {op_query}"
            )
        op_path_params = [p["name"] for p in op.get("parameters", []) if p.get("in") == "path"]
        if entry["pathParams"] != op_path_params:
            errors.append(
                f"endpoints.json {method} {path}: pathParams {entry['pathParams']} != "
                f"{op_path_params}"
            )
        if bool(op.get("security")) != bool(entry["requiresAuth"]):
            errors.append(f"endpoints.json {method} {path}: requiresAuth mismatch")

    # 5. resources.ts apiFetch calls vs openapi.yaml & endpoints.json
    client_calls = _parse_resources_api_calls(resources_text)
    client_by_key: dict[tuple[str, str], str] = {}
    for fn_name, method, norm_path in client_calls:
        openapi_norm_path = "/api" + norm_path
        matching_openapi = next(
            (
                p
                for p in spec.get("paths", {})
                if re.sub(r"\{[^}]+\}", "{}", p) == openapi_norm_path
            ),
            None,
        )
        if not matching_openapi:
            errors.append(
                f"resources.ts {fn_name} calls {method} {norm_path}, missing from openapi.yaml"
            )
            continue
        if method.lower() not in spec["paths"][matching_openapi]:
            errors.append(
                f"resources.ts {fn_name} uses method {method} on {matching_openapi}, "
                "missing from openapi.yaml"
            )
            continue
        client_by_key[(method, matching_openapi)] = fn_name

    for key, entry in sorted(manifest_by_key.items()):
        if entry.get("opsOnly"):
            continue
        expected_fn = entry.get("resourceFn")
        actual_fn = client_by_key.get(key)
        if actual_fn != expected_fn:
            errors.append(
                f"resources.ts drift for {key}: expected fn {expected_fn!r}, found {actual_fn!r}"
            )

    return errors


def validate_fixture_corpus(
    spec: dict[str, Any],
    fixtures: dict[str, Any],
) -> list[str]:
    """Validate canonical contract fixtures against OpenAPI schemas."""
    errors: list[str] = []
    schemas = spec.get("components", {}).get("schemas", {})
    valid_map = fixtures.get("valid", {})
    invalid_map = fixtures.get("invalid", {})

    expected_json_schemas = {name for name in schemas if not name.startswith("Body_upload_import_")}
    missing_fixture_schemas = sorted(expected_json_schemas - set(valid_map))
    if missing_fixture_schemas:
        errors.append(f"schemas.json missing valid fixtures for: {missing_fixture_schemas}")

    for schema_name, cases in sorted(valid_map.items()):
        if schema_name not in schemas:
            errors.append(f"schemas.json valid references unknown schema {schema_name!r}")
            continue
        for case in cases:
            case_errors = validate_against_schema(
                case["data"],
                {"$ref": f"#/components/schemas/{schema_name}"},
                spec,
                allow_extra_properties=False,
                path=f"{schema_name}[{case['name']}]",
            )
            errors.extend(case_errors)

    for schema_name, cases in sorted(invalid_map.items()):
        if schema_name not in schemas:
            errors.append(f"schemas.json invalid references unknown schema {schema_name!r}")
            continue
        for case in cases:
            case_errors = validate_against_schema(
                case["data"],
                {"$ref": f"#/components/schemas/{schema_name}"},
                spec,
                allow_extra_properties=False,
                path=f"{schema_name}[{case['name']}]",
            )
            if not case_errors:
                errors.append(
                    f"Invalid fixture {schema_name}[{case['name']}] unexpectedly passed validation"
                )

    return errors


def run_all_checks(repo_root: Path | None = None) -> list[str]:
    root = repo_root or default_repo_root()
    committed_spec = load_json_file(root / "openapi.yaml")
    live_spec = app.openapi()
    schemas_fixtures = load_json_file(root / "contracts/fixtures/schemas.json")
    endpoints_manifest = load_json_file(root / "contracts/fixtures/endpoints.json")

    errors: list[str] = []
    errors.extend(validate_live_openapi_parity(live_spec, committed_spec))
    errors.extend(validate_openapi_invariants(committed_spec))
    errors.extend(validate_frontend_contract_drift(root, committed_spec, endpoints_manifest))
    errors.extend(validate_fixture_corpus(committed_spec, schemas_fixtures))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate OpenAPI schema conformance and frontend/backend contract drift."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=default_repo_root(),
        help="Repository root path",
    )
    args = parser.parse_args()
    errors = run_all_checks(args.repo_root)
    if errors:
        print(f"Contract drift detected ({len(errors)} issue(s)):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(
        "Contract drift check passed: live OpenAPI, openapi.yaml, frontend schema.gen.ts, "
        "resources.ts, types.ts, and contract fixtures are aligned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
