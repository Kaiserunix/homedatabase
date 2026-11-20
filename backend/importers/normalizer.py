from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .models import (
    MAX_SAFE_MINOR,
    SHANGHAI_TZ,
    MinorAmountError,
    NormalizedBillRecord,
)

_HEADER_CLEAN_RE = re.compile(r"[\s\-_./\\:：()（）\[\]【】{}]+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00a0]")
_WHITESPACE_RE = re.compile(r"\s+")
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

_CHANNEL_PREFIX_RE = re.compile(
    r"^(?:财付通|支付宝|微信支付|特约商户|快捷支付|网上支付|银联在线|POS消费|消费|代扣)"
    r"[\s\-—_*·:：]+",
    re.IGNORECASE,
)
_ASCII_CHANNEL_PREFIX_RE = re.compile(
    r"^(?:alipay|wechat\s*pay|tenpay|unionpay|pos|ach|sq)\s*[*_\-:]+\s*",
    re.IGNORECASE,
)

_PLACEHOLDER_IDS = frozenset({"", "/", "-", "--", "无", "null", "none", "n/a", "na"})

_CURRENCY_ALIASES: dict[str, str] = {
    "人民币": "CNY",
    "元": "CNY",
    "RMB": "CNY",
    "CNH": "CNY",
    "¥": "CNY",
    "￥": "CNY",
    "美元": "USD",
    "美金": "USD",
    "US$": "USD",
    "港币": "HKD",
    "港元": "HKD",
    "HK$": "HKD",
    "欧元": "EUR",
    "€": "EUR",
    "英镑": "GBP",
    "£": "GBP",
    "日元": "JPY",
    "日币": "JPY",
}

_CURRENCY_TOKEN_STRIPS = (
    "HK$",
    "US$",
    "CNY",
    "RMB",
    "USD",
    "HKD",
    "EUR",
    "GBP",
    "JPY",
    "人民币",
    "美元",
    "港币",
    "元",
    "¥",
    "￥",
    "$",
    "€",
    "£",
)

# Maps common Alipay / WeChat / Bank statement category labels onto HomeDatabase's
# canonical seeded categories:
# Expense: 餐饮, 买菜超市, 交通, 居家, 医疗, 教育, 娱乐, 其他
# Income:  工资, 奖金, 理财收益, 其他收入
DEFAULT_CATEGORY_ALIASES: dict[tuple[str, str], str] = {
    # Expense aliases
    ("expense", "餐饮"): "餐饮",
    ("expense", "餐饮美食"): "餐饮",
    ("expense", "美食"): "餐饮",
    ("expense", "外卖"): "餐饮",
    ("expense", "堂食"): "餐饮",
    ("expense", "咖啡茶饮"): "餐饮",
    ("expense", "food"): "餐饮",
    ("expense", "dining"): "餐饮",
    ("expense", "restaurants"): "餐饮",
    ("expense", "买菜超市"): "买菜超市",
    ("expense", "日用百货"): "买菜超市",
    ("expense", "商超便利"): "买菜超市",
    ("expense", "生鲜食品"): "买菜超市",
    ("expense", "超市"): "买菜超市",
    ("expense", "买菜"): "买菜超市",
    ("expense", "百货"): "买菜超市",
    ("expense", "groceries"): "买菜超市",
    ("expense", "supermarket"): "买菜超市",
    ("expense", "交通"): "交通",
    ("expense", "交通出行"): "交通",
    ("expense", "出行"): "交通",
    ("expense", "打车出行"): "交通",
    ("expense", "公共交通"): "交通",
    ("expense", "加油养车"): "交通",
    ("expense", "爱车养车"): "交通",
    ("expense", "机票酒旅"): "交通",
    ("expense", "transport"): "交通",
    ("expense", "transit"): "交通",
    ("expense", "居家"): "居家",
    ("expense", "住房物业"): "居家",
    ("expense", "生活缴费"): "居家",
    ("expense", "充值缴费"): "居家",
    ("expense", "水电煤"): "居家",
    ("expense", "水电网"): "居家",
    ("expense", "家居家装"): "居家",
    ("expense", "房租"): "居家",
    ("expense", "通讯物流"): "居家",
    ("expense", "utilities"): "居家",
    ("expense", "housing"): "居家",
    ("expense", "医疗"): "医疗",
    ("expense", "医疗健康"): "医疗",
    ("expense", "医药卫生"): "医疗",
    ("expense", "看病买药"): "医疗",
    ("expense", "medical"): "医疗",
    ("expense", "healthcare"): "医疗",
    ("expense", "教育"): "教育",
    ("expense", "教育培训"): "教育",
    ("expense", "文教体娱"): "教育",
    ("expense", "书籍学习"): "教育",
    ("expense", "education"): "教育",
    ("expense", "娱乐"): "娱乐",
    ("expense", "文化休闲"): "娱乐",
    ("expense", "休闲娱乐"): "娱乐",
    ("expense", "数码电器"): "娱乐",
    ("expense", "运动户外"): "娱乐",
    ("expense", "旅游度假"): "娱乐",
    ("expense", "影音会员"): "娱乐",
    ("expense", "entertainment"): "娱乐",
    ("expense", "其他"): "其他",
    ("expense", "服饰装扮"): "其他",
    ("expense", "美容美发"): "其他",
    ("expense", "母婴亲子"): "其他",
    ("expense", "人情往来"): "其他",
    ("expense", "转账红包"): "其他",
    ("expense", "微信红包"): "其他",
    ("expense", "商户消费"): "其他",
    ("expense", "扫二维码付款"): "其他",
    ("expense", "other"): "其他",
    # Income aliases
    ("income", "工资"): "工资",
    ("income", "薪酬福利"): "工资",
    ("income", "代发工资"): "工资",
    ("income", "薪资"): "工资",
    ("income", "salary"): "工资",
    ("income", "payroll"): "工资",
    ("income", "奖金"): "奖金",
    ("income", "补贴"): "奖金",
    ("income", "年终奖"): "奖金",
    ("income", "bonus"): "奖金",
    ("income", "理财收益"): "理财收益",
    ("income", "投资理财"): "理财收益",
    ("income", "利息收入"): "理财收益",
    ("income", "结息"): "理财收益",
    ("income", "分红"): "理财收益",
    ("income", "余额宝"): "理财收益",
    ("income", "interest"): "理财收益",
    ("income", "dividend"): "理财收益",
    ("income", "其他收入"): "其他收入",
    ("income", "转账红包"): "其他收入",
    ("income", "微信红包"): "其他收入",
    ("income", "收钱码收款"): "其他收入",
    ("income", "二维码收款"): "其他收入",
    ("income", "otherincome"): "其他收入",
}

_EXPENSE_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "美团外卖",
            "饿了么",
            "肯德基",
            "麦当劳",
            "星巴克",
            "starbucks",
            "瑞幸",
            "喜茶",
            "霸王茶姬",
            "海底捞",
            "餐厅",
            "饭店",
            "酒楼",
            "面馆",
            "烧烤",
            "食堂",
            "餐饮",
            "咖啡",
        ),
        "餐饮",
    ),
    (
        (
            "盒马",
            "山姆",
            "沃尔玛",
            "大润发",
            "永辉",
            "物美",
            "联华",
            "华润万家",
            "罗森",
            "全家",
            "7-eleven",
            "711",
            "便利店",
            "超市",
            "菜市场",
            "买菜",
            "叮咚买菜",
            "朴朴",
            "水果",
        ),
        "买菜超市",
    ),
    (
        (
            "地铁",
            "公交",
            "滴滴",
            "高德打车",
            "曹操出行",
            "哈啰",
            "青桔",
            "美团单车",
            "12306",
            "铁路",
            "高铁",
            "航空",
            "机票",
            "中石化",
            "中石油",
            "停车",
            "高速",
            "etc",
        ),
        "交通",
    ),
    (
        (
            "国家电网",
            "供电",
            "自来水",
            "水务",
            "燃气",
            "物业",
            "房租",
            "宽带",
            "中国移动",
            "中国联通",
            "中国电信",
            "宜家",
            "顺丰",
            "菜鸟",
        ),
        "居家",
    ),
    (
        (
            "医院",
            "诊所",
            "药房",
            "大药房",
            "医药",
            "体检",
            "齿科",
            "口腔",
            "卫生服务",
        ),
        "医疗",
    ),
    (
        (
            "大学",
            "学校",
            "学费",
            "书店",
            "培训",
            "网课",
            "考试",
            "coursera",
        ),
        "教育",
    ),
    (
        (
            "电影",
            "影城",
            "steam",
            "playstation",
            "nintendo",
            "网易云音乐",
            "qq音乐",
            "爱奇艺",
            "腾讯视频",
            "哔哩哔哩",
            "bilibili",
            "优酷",
            "ktv",
            "游乐园",
        ),
        "娱乐",
    ),
)

_INCOME_KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("工资", "薪资", "代发", "payroll", "salary"), "工资"),
    (("奖金", "年终奖", "绩效", "津贴", "bonus"), "奖金"),
    (
        ("利息", "结息", "收益", "分红", "理财", "余额宝", "零钱通", "interest", "dividend"),
        "理财收益",
    ),
)


def decode_text_bytes(
    raw: bytes,
    *,
    encodings: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "cp1252"),
) -> tuple[str | None, str | None]:
    """Safely decode statement bytes while rejecting binary files with NUL bytes."""
    if b"\x00" in raw[:8192]:
        return None, "binary_payload"
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None, "unknown_encoding"


def clean_cell_text(value: object | None) -> str | None:
    """Normalize cell text, stripping Excel export padding, tabs, and guard quotes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _ZERO_WIDTH_RE.sub(" ", str(value))
    # Strip outer tabs/spaces and Excel leading single-quote text guards (e.g. '420000123\t)
    text = text.strip(" \t\r\n")
    if len(text) >= 2 and text.startswith("'") and text[1:].strip().isdigit():
        text = text[1:]
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def clean_external_id(value: object | None) -> str | None:
    """Extract a clean institution/platform transaction identifier."""
    text = clean_cell_text(value)
    if text is None:
        return None
    # Strip trailing tabs/quotes sometimes wrapped inside CSV quotes
    cleaned = text.strip(" \t'\"")
    if cleaned.casefold() in _PLACEHOLDER_IDS:
        return None
    return cleaned[:160]


def canonicalize_payee(payee: str | None) -> str:
    """Produce a normalized merchant/counterparty key for cross-channel dedup & memory."""
    if not payee:
        return ""
    text = unicodedata.normalize("NFKC", payee)
    text = _ZERO_WIDTH_RE.sub(" ", text).strip()
    # Repeatedly strip payment gateway wrappers such as "财付通-微信支付-美团"
    for _ in range(3):
        updated = _CHANNEL_PREFIX_RE.sub("", text)
        updated = _ASCII_CHANNEL_PREFIX_RE.sub("", updated).strip()
        if updated == text:
            break
        text = updated
    text = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return text


def normalize_header_token(value: object | None) -> str:
    text = clean_cell_text(value) or ""
    normalised = unicodedata.normalize("NFKC", text).strip().casefold()
    return _HEADER_CLEAN_RE.sub("", normalised)


def parse_minor_amount(value: object | None) -> int | None:
    """Parse major-unit amount into signed integer minor units with strict precision checks."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if math.ulp(value) >= 0.01:
            raise MinorAmountError("ambiguous_xlsx_float_amount")
    text = clean_cell_text(value)
    if text is None or text in {"/", "-", "--"}:
        return None
    normalised = unicodedata.normalize("NFKC", text).strip()
    negative_parentheses = normalised.startswith("(") and normalised.endswith(")")
    if negative_parentheses:
        normalised = normalised[1:-1].strip()
    normalised = normalised.replace(",", "").replace("，", "").replace(" ", "")
    sign = ""
    if normalised.startswith(("+", "-")):
        sign = normalised[0]
        normalised = normalised[1:].strip()
    for token in _CURRENCY_TOKEN_STRIPS:
        normalised = re.sub(re.escape(token), "", normalised, flags=re.IGNORECASE).strip()
    if normalised.startswith(("+", "-")) and not sign:
        sign = normalised[0]
        normalised = normalised[1:].strip()
    if negative_parentheses:
        sign = "-"
    if not normalised:
        return None
    try:
        amount = Decimal(f"{sign}{normalised}")
    except InvalidOperation:
        return None
    if not amount.is_finite():
        return None
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise MinorAmountError("amount_has_more_than_two_decimal_places")
    minor = int(scaled)
    if abs(minor) > MAX_SAFE_MINOR:
        raise MinorAmountError("amount_exceeds_safe_range")
    return minor


def parse_currency_code(
    value: object | None,
    *,
    default_currency: str = "CNY",
) -> tuple[str | None, bool]:
    """Return (ISO-4217 code | None, was_defaulted)."""
    raw = clean_cell_text(value)
    if not raw or raw in {"/", "-"}:
        return default_currency.upper(), True
    token = unicodedata.normalize("NFKC", raw).strip().upper()
    token = _CURRENCY_ALIASES.get(token, _CURRENCY_ALIASES.get(raw.strip(), token))
    if _ISO_CURRENCY_RE.fullmatch(token):
        return token, False
    return None, False


_OFX_TS_RE = re.compile(
    r"^(?P<ymd>\d{8})"
    r"(?:(?P<hms>\d{4}|\d{6})(?:\.(?P<ms>\d{1,6}))?)?"
    r"(?:\s*\[(?P<offset>[+\-]?\d+(?:\.\d+)?)(?::[^\]]*)?\])?"
    r"(?P<z>Z)?$",
    re.IGNORECASE,
)


def parse_ofx_datetime(
    value: object | None,
    *,
    default_tz: ZoneInfo = SHANGHAI_TZ,
) -> datetime | None:
    """Parse OFX 1.x / 2.x timestamps such as 20260805123000.000[+8:CST] or 20260805."""
    text = clean_cell_text(value)
    if not text:
        return None
    match = _OFX_TS_RE.match(text)
    if not match:
        return parse_statement_datetime(text, default_tz=default_tz)
    ymd = match.group("ymd")
    hms = match.group("hms") or "000000"
    if len(hms) == 4:
        hms = f"{hms}00"
    try:
        dt = datetime.strptime(f"{ymd}{hms}", "%Y%m%d%H%M%S")
    except ValueError:
        return None
    offset_str = match.group("offset")
    if match.group("z"):
        return dt.replace(tzinfo=UTC)
    if offset_str is not None:
        try:
            hours = float(offset_str)
            tz = timezone(timedelta(minutes=round(hours * 60)))
            return dt.replace(tzinfo=tz)
        except (ValueError, OverflowError):
            return None
    return dt.replace(tzinfo=default_tz)


def parse_statement_datetime(
    value: object | None,
    *,
    default_tz: ZoneInfo = SHANGHAI_TZ,
) -> datetime | None:
    """Parse statement timestamps into timezone-aware datetimes."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=default_tz)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=default_tz)
    text = clean_cell_text(value)
    if not text or text in {"/", "-", "--"}:
        return None
    cleaned = (
        unicodedata.normalize("NFKC", text)
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", " ")
        .replace(".", "-")
        .replace("/", "-")
    )
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=default_tz)
    except ValueError:
        pass
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d %H%M%S",
        "%Y%m%d%H%M%S",
        "%Y%m%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
        "%m-%d-%Y",
    ):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=default_tz)
        except ValueError:
            continue
    return None


def combine_date_and_time(
    date_value: object | None,
    time_value: object | None,
    *,
    default_tz: ZoneInfo = SHANGHAI_TZ,
) -> datetime | None:
    """Combine split bank date + time columns (e.g. '20260805' + '12:30:15' or '123015')."""
    base = parse_statement_datetime(date_value, default_tz=default_tz)
    if base is None:
        return None
    time_text = clean_cell_text(time_value)
    if not time_text or time_text in {"/", "-", "--"}:
        return base
    # If date_value already carried non-midnight time, preserve it
    if base.hour != 0 or base.minute != 0 or base.second != 0:
        return base
    compact = unicodedata.normalize("NFKC", time_text).strip()
    for pattern in ("%H:%M:%S", "%H:%M", "%H%M%S", "%H%M"):
        try:
            parsed_time = datetime.strptime(compact, pattern).time()
            return base.replace(
                hour=parsed_time.hour,
                minute=parsed_time.minute,
                second=parsed_time.second,
            )
        except ValueError:
            continue
    return base


def normalize_to_utc(
    value: datetime,
    *,
    default_tz: ZoneInfo = SHANGHAI_TZ,
) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=default_tz)
    return value.astimezone(UTC)


def compute_source_hash(provider: str, raw_payload: Mapping[str, str | None]) -> str:
    canonical = json.dumps(
        {"provider": provider, "fields": dict(raw_payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_legacy_compatible_row_hash(raw_payload: Mapping[str, str | None]) -> str:
    canonical = json.dumps(
        dict(raw_payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_semantic_fingerprint(
    *,
    occurred_at: datetime | None,
    direction: str | None,
    amount_minor: int | None,
    currency: str,
    payee: str | None,
    default_tz: ZoneInfo = SHANGHAI_TZ,
) -> str:
    utc_iso = (
        normalize_to_utc(occurred_at, default_tz=default_tz).isoformat()
        if occurred_at is not None
        else ""
    )
    payload = {
        "occurred_at_utc": utc_iso,
        "direction": direction or "",
        "amount_minor": amount_minor if amount_minor is not None else -1,
        "currency": currency.upper(),
        "canonical_payee": canonicalize_payee(payee),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def format_parse_summary(
    records: list[NormalizedBillRecord],
    *,
    provider_label: str | None = None,
) -> str:
    total = len(records)
    valid = sum(record.is_ledger_candidate for record in records)
    transfers = sum(record.direction == "transfer" for record in records)
    refunds = sum(record.direction == "refund" for record in records)
    ignored = sum(record.direction == "ignored" for record in records)
    prefix = f"[{provider_label}] " if provider_label else ""
    message = f"{prefix}解析 {total} 行，生成 {valid} 条候选"
    invalid = total - valid
    if not invalid:
        return message

    issue_counts = Counter(issue for record in records for issue in record.issues)
    details: list[str] = []
    if transfers:
        details.append(f"{transfers} 条转账/中性交易需指定对手账户")
    if refunds:
        details.append(f"{refunds} 条退款需关联原支出")
    if ignored:
        details.append(f"{ignored} 条已关闭/不计入账状态已跳过")
    if issue_counts["amount_exceeds_safe_range"]:
        details.append(f"{issue_counts['amount_exceeds_safe_range']} 行金额超出安全范围")
    if issue_counts["ambiguous_xlsx_float_amount"]:
        details.append(f"{issue_counts['ambiguous_xlsx_float_amount']} 行 XLSX 大数精度不明确")
    if issue_counts["amount_has_more_than_two_decimal_places"]:
        details.append(
            f"{issue_counts['amount_has_more_than_two_decimal_places']} 行金额超过两位小数"
        )
    other_invalid = invalid - transfers - refunds - ignored
    if other_invalid > 0 and not any(
        issue_counts[key]
        for key in (
            "amount_exceeds_safe_range",
            "ambiguous_xlsx_float_amount",
            "amount_has_more_than_two_decimal_places",
        )
    ):
        details.append(f"{other_invalid} 行字段不完整")
    detail_str = "；".join(details) or f"{invalid} 行未生成普通收支候选"
    return f"{message}，{detail_str}，已保留原始记录但未生成候选"


class CategoryResolver:
    """Decoupled category normalizer combining exact match, payee memory, aliases & keywords."""

    def __init__(
        self,
        *,
        catalog: Mapping[tuple[str, str], str] | None = None,
        payee_memory: Mapping[tuple[str, str], str] | Mapping[str, str] | None = None,
        custom_aliases: Mapping[tuple[str, str], str] | None = None,
        enable_keyword_rules: bool = True,
    ) -> None:
        # catalog maps (direction, casefolded_category_name) -> category_id
        self._catalog: dict[tuple[str, str], str] = {
            (kind.strip().casefold(), name.strip().casefold()): cat_id
            for (kind, name), cat_id in (catalog or {}).items()
        }
        # Reverse map for returning canonical display name when catalog is provided
        self._id_to_name: dict[str, str] = {
            cat_id: name.strip() for (_kind, name), cat_id in (catalog or {}).items()
        }
        self._aliases: dict[tuple[str, str], str] = {
            (kind, normalize_header_token(raw_name)): target
            for (kind, raw_name), target in DEFAULT_CATEGORY_ALIASES.items()
        }
        if custom_aliases:
            for (kind, raw_name), target in custom_aliases.items():
                self._aliases[(kind.casefold(), normalize_header_token(raw_name))] = target

        self._scoped_memory: dict[tuple[str, str], str] = {}
        self._unscoped_memory: dict[str, str] = {}
        if payee_memory:
            for key, target in payee_memory.items():
                if isinstance(key, tuple):
                    direction, raw_payee = key
                    canon = canonicalize_payee(raw_payee)
                    if canon:
                        self._scoped_memory[(direction.casefold(), canon)] = target
                else:
                    canon = canonicalize_payee(key)
                    if canon:
                        self._unscoped_memory[canon] = target
        self._enable_keyword_rules = enable_keyword_rules

    def resolve(
        self,
        record: NormalizedBillRecord,
    ) -> tuple[str | None, str | None]:
        """Return (suggested_category_name, suggested_category_id) for a normalized record."""
        if record.direction not in {"expense", "income"}:
            return None, None
        direction = record.direction
        generic_buckets = {"其他", "其他收入", "other", "otherincome"}
        orig_clean = (record.original_category or "").strip()
        orig_is_generic = orig_clean.casefold() in generic_buckets

        # 1. Specific exact catalog match on bill's original category (non-catch-all)
        if orig_clean and not orig_is_generic:
            exact_key = (direction, orig_clean.casefold())
            if exact_key in self._catalog:
                cat_id = self._catalog[exact_key]
                return self._id_to_name.get(cat_id, orig_clean), cat_id

        # 2. Historical user-confirmed payee -> category memory (highest UX leverage)
        if record.canonical_payee:
            remembered = self._scoped_memory.get(
                (direction, record.canonical_payee)
            ) or self._unscoped_memory.get(record.canonical_payee)
            if remembered:
                return self._bind_target(direction, remembered)

        # 3. Platform category alias mapping (e.g. Alipay "餐饮美食" -> "餐饮")
        if orig_clean:
            alias_key = (direction, normalize_header_token(orig_clean))
            mapped_name = self._aliases.get(alias_key)
            if mapped_name and mapped_name not in {"其他", "其他收入"}:
                return self._bind_target(direction, mapped_name)
            # If the platform category was generic ("商户消费", "其他"), try keywords before
            # falling back to "其他"
            if self._enable_keyword_rules:
                keyword_hit = self._match_keywords(direction, record)
                if keyword_hit:
                    return self._bind_target(direction, keyword_hit)
            if orig_is_generic and (direction, orig_clean.casefold()) in self._catalog:
                cat_id = self._catalog[(direction, orig_clean.casefold())]
                return self._id_to_name.get(cat_id, orig_clean), cat_id
            if mapped_name:
                return self._bind_target(direction, mapped_name)

        # 4. Merchant + item summary keyword inference
        if self._enable_keyword_rules:
            keyword_hit = self._match_keywords(direction, record)
            if keyword_hit:
                return self._bind_target(direction, keyword_hit)

        return None, None

    def enrich_records(
        self,
        records: list[NormalizedBillRecord],
    ) -> list[NormalizedBillRecord]:
        enriched: list[NormalizedBillRecord] = []
        for record in records:
            cat_name, cat_id = self.resolve(record)
            if cat_name is not None or cat_id is not None:
                enriched.append(record.with_category(category_name=cat_name, category_id=cat_id))
            else:
                enriched.append(record)
        return enriched

    def _bind_target(self, direction: str, target: str) -> tuple[str | None, str | None]:
        # Target might already be a category_id or a canonical category name
        if target in self._id_to_name:
            return self._id_to_name[target], target
        cat_id = self._catalog.get((direction, target.strip().casefold()))
        if self._catalog and cat_id is None:
            return target, None
        return target, cat_id

    @staticmethod
    def _match_keywords(direction: str, record: NormalizedBillRecord) -> str | None:
        haystack = " ".join(
            part.casefold()
            for part in (record.payee, record.item_summary, record.original_category)
            if part
        )
        if not haystack:
            return None
        rules = _EXPENSE_KEYWORD_RULES if direction == "expense" else _INCOME_KEYWORD_RULES
        for keywords, category_name in rules:
            if any(keyword in haystack for keyword in keywords):
                return category_name
        return None
