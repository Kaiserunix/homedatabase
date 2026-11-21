from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass

from .models import FormatSecurityError, ImportDocument, ImportLimitError, ParserLimits
from .normalizer import clean_cell_text, decode_text_bytes


@dataclass(slots=True, frozen=True)
class RawTableSheet:
    name: str
    rows: list[list[object]]


def read_delimited_sheet(
    doc: ImportDocument,
    limits: ParserLimits,
    *,
    source_name: str = "CSV",
) -> RawTableSheet:
    if doc.size_bytes > limits.max_file_bytes:
        raise ImportLimitError(f"文件大小超过上限 ({limits.max_file_bytes} bytes)")
    text, reason = decode_text_bytes(doc.raw_bytes)
    if text is None:
        if reason == "binary_payload":
            raise FormatSecurityError("CSV/TSV 看起来是二进制文件")
        raise ValueError("无法识别文本编码，请导出为 UTF-8 或 GB18030 CSV")

    sample = text[:16384]
    if doc.suffix == ".tsv":
        delimiter = "\t"
    else:
        delimiter = _sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[list[object]] = []
    # Allow up to 60 preamble/footer lines in addition to max_rows data rows
    hard_cap = limits.max_rows + 80
    for idx, row in enumerate(reader, start=1):
        if idx > hard_cap:
            raise ImportLimitError(f"账单超过 {limits.max_rows} 行")
        rows.append(list(row))
    return RawTableSheet(name=source_name, rows=rows)


def _sniff_delimiter(sample: str) -> str:
    # Prefer comma when preamble has prose lines without delimiters followed by comma headers
    lines = [line for line in sample.splitlines() if line.strip()]
    comma_rich = sum(1 for line in lines if line.count(",") >= 4)
    tab_rich = sum(1 for line in lines if line.count("\t") >= 4 and line.count(",") < 2)
    if comma_rich >= 1 and comma_rich >= tab_rich:
        return ","
    if tab_rich >= 1:
        return "\t"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return "\t" if "\t" in sample and "," not in sample else ","


def read_xlsx_sheets(
    doc: ImportDocument,
    limits: ParserLimits,
) -> list[RawTableSheet]:
    if doc.size_bytes > limits.max_file_bytes:
        raise ImportLimitError(f"文件大小超过上限 ({limits.max_file_bytes} bytes)")

    buffer = io.BytesIO(doc.raw_bytes)
    try:
        with zipfile.ZipFile(buffer) as archive:
            members = archive.infolist()
            expanded = sum(member.file_size for member in members)
            if len(members) > 10_000 or expanded > limits.max_uncompressed_bytes:
                raise FormatSecurityError("XLSX 解压后过大，已拒绝处理")
            for member in members:
                if member.compress_size == 0:
                    continue
                if member.file_size > member.compress_size * 500:
                    raise FormatSecurityError("XLSX 压缩比异常，已拒绝处理")
    except zipfile.BadZipFile as exc:
        raise FormatSecurityError("XLSX 文件已损坏或扩展名与内容不符") from exc

    from openpyxl import load_workbook

    workbook_stream = io.BytesIO(doc.raw_bytes)
    try:
        workbook = load_workbook(workbook_stream, read_only=True, data_only=True)
    except Exception as exc:
        workbook_stream.close()
        raise ValueError(f"无法读取 XLSX：{type(exc).__name__}") from exc

    sheets: list[RawTableSheet] = []
    total_rows = 0
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[object]] = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                if index > limits.max_rows + 80:
                    raise ImportLimitError(f"单个工作表超过 {limits.max_rows} 行")
                rows.append(list(row))
            total_rows += len(rows)
            if total_rows > limits.max_rows + 200:
                raise ImportLimitError(f"工作簿超过 {limits.max_rows} 行")
            sheets.append(RawTableSheet(name=worksheet.title, rows=rows))
    finally:
        workbook.close()
        workbook_stream.close()
    return sheets


def read_document_sheets(doc: ImportDocument, limits: ParserLimits) -> list[RawTableSheet]:
    if doc.suffix == ".xlsx":
        return read_xlsx_sheets(doc, limits)
    return [read_delimited_sheet(doc, limits, source_name="CSV")]


def unique_headers(row: list[object]) -> list[str]:
    base_headers = [clean_cell_text(val) or f"列{idx + 1}" for idx, val in enumerate(row)]
    result: list[str] = []
    for idx, header in enumerate(base_headers):
        prior = base_headers[:idx].count(header)
        result.append(header if prior == 0 else f"{header}#{prior + 1}")
    return result


def build_raw_payload(headers: list[str], values: list[object]) -> dict[str, str | None]:
    return {
        headers[idx]: clean_cell_text(val) for idx, val in enumerate(values) if idx < len(headers)
    }


def cell_at(values: list[object], index: int | None) -> object | None:
    if index is None or index < 0 or index >= len(values):
        return None
    return values[index]
