from __future__ import annotations

from pathlib import Path

from .alipay import AlipayBillParser
from .csv_parser import GenericTableBillParser
from .models import (
    BillParserPlugin,
    ImportDocument,
    ImportLimitError,
    ImportParseResult,
    ParserLimits,
    ProviderId,
)
from .ofx import OfxBillParser
from .wechat import WeChatBillParser

_OCR_EXTENSIONS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"})


class OcrPassthroughParser:
    """Validates binary magic headers for PDF/image bills and routes them to needs_ocr."""

    provider_id: ProviderId = "ocr_passthrough"
    priority = 110

    def sniff(self, doc: ImportDocument) -> float:
        if doc.suffix in _OCR_EXTENSIONS:
            return 1.0
        return 0.0

    def parse(self, doc: ImportDocument, limits: ParserLimits) -> ImportParseResult:
        if doc.size_bytes > limits.max_file_bytes:
            return ImportParseResult(
                "ocr_passthrough",
                "rejected",
                f"文件大小超过上限 ({limits.max_file_bytes} bytes)",
            )
        if not self._has_expected_signature(doc.raw_bytes[:16], doc.suffix):
            return ImportParseResult("ocr_passthrough", "rejected", "文件扩展名与内容不符")
        return ImportParseResult(
            "ocr_passthrough",
            "needs_ocr",
            "文件已安全保存，等待多模态/OCR 工作器处理",
        )

    @staticmethod
    def _has_expected_signature(head: bytes, suffix: str) -> bool:
        if suffix == ".pdf":
            return head.startswith(b"%PDF-")
        if suffix == ".png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        if suffix in {".jpg", ".jpeg"}:
            return head.startswith(b"\xff\xd8\xff")
        if suffix == ".webp":
            return len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP"
        if suffix == ".heic":
            return len(head) >= 12 and head[4:12] in {b"ftypheic", b"ftypheix", b"ftypmif1"}
        return False


class ParserRegistry:
    """Pluggable bill-import parser registry with content-based sniffing and priority routing."""

    def __init__(self, plugins: list[BillParserPlugin] | None = None) -> None:
        self._plugins: list[BillParserPlugin] = []
        for plugin in plugins or []:
            self.register(plugin)

    @property
    def plugins(self) -> tuple[BillParserPlugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: BillParserPlugin) -> None:
        if not isinstance(plugin, BillParserPlugin):
            raise TypeError(f"{plugin!r} does not satisfy BillParserPlugin protocol")
        self._plugins = [
            existing for existing in self._plugins if existing.provider_id != plugin.provider_id
        ]
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda item: item.priority, reverse=True)

    def unregister(self, provider_id: ProviderId | str) -> bool:
        before = len(self._plugins)
        self._plugins = [p for p in self._plugins if p.provider_id != provider_id]
        return len(self._plugins) < before

    def get(self, provider_id: ProviderId | str) -> BillParserPlugin | None:
        for plugin in self._plugins:
            if plugin.provider_id == provider_id:
                return plugin
        return None

    def detect(self, doc: ImportDocument) -> BillParserPlugin | None:
        best_score = 0.0
        best_plugin: BillParserPlugin | None = None
        for plugin in self._plugins:
            score = plugin.sniff(doc)
            if score <= 0.0:
                continue
            # Weighted tie-breaker: confidence dominates, plugin.priority breaks ties
            weighted = score + (plugin.priority / 10_000.0)
            if weighted > best_score:
                best_score = weighted
                best_plugin = plugin
        return best_plugin

    def parse(
        self,
        doc: ImportDocument,
        *,
        limits: ParserLimits | None = None,
        preferred_provider: ProviderId | None = None,
    ) -> ImportParseResult:
        effective_limits = limits or ParserLimits()
        if doc.suffix == ".xls":
            return ImportParseResult(
                "unknown",
                "rejected",
                "暂不安全解析旧版 XLS，请另存为 XLSX 后重新导入",
            )

        if preferred_provider is not None:
            explicit = self.get(preferred_provider)
            if explicit is None:
                return ImportParseResult(
                    "unknown",
                    "rejected",
                    f"未注册指定的解析器：{preferred_provider}",
                )
            return explicit.parse(doc, effective_limits)

        selected = self.detect(doc)
        if selected is None:
            return ImportParseResult(
                "unknown",
                "rejected",
                "不支持该文件格式；当前可解析微信/支付宝/银行 CSV、TSV、XLSX 与 OFX/QFX",
            )

        result = selected.parse(doc, effective_limits)
        # Graceful fallback: if a specialized CSV/XLSX plugin sniffed positively only by
        # filename hint (e.g. a user renamed a generic template to `微信测试.csv`), let
        # GenericTableBillParser attempt parsing before rejecting.
        if (
            result.status == "rejected"
            and selected.provider_id in {"wechat", "alipay"}
            and doc.suffix in {".csv", ".tsv", ".xlsx"}
        ):
            generic = self.get("csv")
            if generic is not None:
                fallback = generic.parse(doc, effective_limits)
                if fallback.status == "parsed":
                    return fallback
        return result

    def parse_path(
        self,
        path: Path,
        filename: str | None = None,
        *,
        limits: ParserLimits | None = None,
        source_hint: str | None = None,
        preferred_provider: ProviderId | None = None,
    ) -> ImportParseResult:
        effective_limits = limits or ParserLimits()
        try:
            doc = ImportDocument.from_path(
                path,
                filename=filename,
                max_file_bytes=effective_limits.max_file_bytes,
                source_hint=source_hint,
            )
        except ImportLimitError as exc:
            return ImportParseResult("unknown", "rejected", str(exc))
        return self.parse(
            doc,
            limits=effective_limits,
            preferred_provider=preferred_provider,
        )


def create_default_registry() -> ParserRegistry:
    return ParserRegistry(
        plugins=[
            OcrPassthroughParser(),
            WeChatBillParser(),
            AlipayBillParser(),
            OfxBillParser(),
            GenericTableBillParser(),
        ]
    )
