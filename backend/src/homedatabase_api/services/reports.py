from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from io import BytesIO
from zoneinfo import ZoneInfo

import xlsxwriter
from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Account, Category, EntryLine, Member, Transaction
from ..schemas import ReportTierOut
from .permissions import (
    account_permission,
    can_view_transaction,
    transaction_has_hidden_accounts,
    visible_transaction_account_ids,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

REPORT_TIERS = [
    ReportTierOut(tier=1, name="阅读账单", audience="日常查看", description="最简单的收支流水"),
    ReportTierOut(tier=2, name="交易明细", audience="完整追溯", description="增加来源和业务字段"),
    ReportTierOut(tier=3, name="复式分录", audience="专业核查", description="逐行展示借贷分录"),
    ReportTierOut(
        tier=4, name="核对汇总", audience="验真", description="核对账户期初、流入、流出和期末"
    ),
    ReportTierOut(tier=5, name="统计分析", audience="分析", description="分类结构和月度收支趋势"),
]


def report_tiers() -> list[ReportTierOut]:
    return REPORT_TIERS


def _minor_text(value: int) -> str:
    sign = "-" if value < 0 else ""
    major, minor = divmod(abs(value), 100)
    return f"{sign}{major}.{minor:02d}"


def _scaled_chart_values(values: list[int]) -> list[int]:
    """Produce bounded exact integers for charts; visible cells retain exact money text."""
    if not values:
        return []
    peak = max(abs(value) for value in values)
    divisor = max(1, (peak + 999_999) // 1_000_000)
    result = []
    for value in values:
        magnitude = (abs(value) + divisor // 2) // divisor
        result.append(-magnitude if value < 0 else magnitude)
    return result


def _visible_transactions(
    db: Session, member: Member, starts_on: date, ends_on: date
) -> list[Transaction]:
    start = datetime.combine(starts_on, time.min, SHANGHAI).astimezone(UTC)
    end = datetime.combine(ends_on, time.max, SHANGHAI).astimezone(UTC)
    transactions = db.scalars(
        select(Transaction)
        .where(
            Transaction.deleted_at.is_(None),
            Transaction.occurred_at >= start,
            Transaction.occurred_at <= end,
        )
        .options(selectinload(Transaction.lines))
        .order_by(Transaction.occurred_at, Transaction.id)
    ).all()
    return [item for item in transactions if can_view_transaction(db, member, item)]


def _assert_balanced(transactions: list[Transaction]) -> None:
    bad = [
        item.id
        for item in transactions
        if sum(line.debit_minor for line in item.lines)
        != sum(line.credit_minor for line in item.lines)
    ]
    if bad:
        raise HTTPException(
            status_code=409,
            detail={"message": "账本存在不平衡分录，已停止导出", "transactionIds": bad},
        )


def _account_name(
    db: Session,
    member: Member,
    account_id: str | None,
    ledger_code: str | None,
) -> str:
    if account_id:
        account = db.get(Account, account_id)
        if account and account_permission(db, member, account) is None:
            return "私人账户（已脱敏）"
        return account.name if account else account_id
    return ledger_code or "—"


def _display_text(db: Session, member: Member, transaction: Transaction) -> tuple[str, str]:
    if transaction_has_hidden_accounts(db, member, transaction):
        return "私人账户往来（已脱敏）", ""
    return transaction.payee, transaction.note


def _opening_balance_at(db: Session, account: Account, starts_at: datetime) -> int:
    signed_native_movement = db.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (EntryLine.debit_minor > 0, EntryLine.account_amount_minor),
                        else_=-EntryLine.account_amount_minor,
                    )
                ),
                0,
            )
        )
        .join(Transaction, Transaction.id == EntryLine.transaction_id)
        .where(
            EntryLine.account_id == account.id,
            Transaction.deleted_at.is_(None),
            Transaction.occurred_at < starts_at,
        )
    )
    movement = int(signed_native_movement or 0)
    if account.kind == "liability":
        movement = -movement
    return account.opening_balance_minor + movement


@contextmanager
def _report_read_session(db: Session, member: Member):
    """Keep every PostgreSQL report query inside one repeatable-read snapshot."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        yield db, member
        return

    member_id = member.id
    with bind.engine.connect() as raw_connection:
        connection = raw_connection.execution_options(isolation_level="REPEATABLE READ")
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as report_db:
            report_member = report_db.get(Member, member_id)
            if report_member is None:
                raise HTTPException(status_code=403, detail="家庭成员不存在")
            yield report_db, report_member


def build_xlsx(
    db: Session,
    member: Member,
    *,
    tier: int,
    starts_on: date,
    ends_on: date,
) -> bytes:
    with _report_read_session(db, member) as (report_db, report_member):
        return _build_xlsx_in_session(
            report_db,
            report_member,
            tier=tier,
            starts_on=starts_on,
            ends_on=ends_on,
        )


def _build_xlsx_in_session(
    db: Session,
    member: Member,
    *,
    tier: int,
    starts_on: date,
    ends_on: date,
) -> bytes:
    if tier not in {1, 2, 3, 4, 5}:
        raise HTTPException(status_code=422, detail="报表层级必须为 1 到 5")
    if starts_on > ends_on:
        raise HTTPException(status_code=422, detail="报表开始日期不能晚于结束日期")
    transactions = _visible_transactions(db, member, starts_on, ends_on)
    _assert_balanced(transactions)
    categories = {item.id: item.name for item in db.scalars(select(Category)).all()}
    accounts = {
        item.id: item
        for item in db.scalars(select(Account).where(Account.deleted_at.is_(None))).all()
        if account_permission(db, member, item) is not None
    }
    starts_at = datetime.combine(starts_on, time.min, SHANGHAI).astimezone(UTC)

    output = BytesIO()
    workbook = xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
            # Imported payees, notes and account names are untrusted. Never let
            # a leading '=' or URL become an executable workbook formula/link.
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    header = workbook.add_format(
        {"bold": True, "bg_color": "#E6F4F1", "font_color": "#115E59", "border": 1}
    )
    money_text = workbook.add_format({"align": "right"})
    date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    title = workbook.add_format({"bold": True, "font_size": 16})
    muted = workbook.add_format({"font_color": "#64748B"})

    readable = workbook.add_worksheet("阅读账单")
    readable.freeze_panes(1, 0)
    readable.autofilter(0, 0, max(1, len(transactions)), 7)
    readable.set_column("A:A", 18)
    readable.set_column("B:B", 28)
    readable.set_column("C:H", 15)
    readable.write_row(
        0,
        0,
        ["日期", "账面名称", "类型", "分类", "金额", "币种", "账户", "备注"],
        header,
    )
    type_names = {"expense": "支出", "income": "收入", "transfer": "转账", "refund": "退款"}
    for row, transaction in enumerate(transactions, start=1):
        visible_ids = visible_transaction_account_ids(db, member, transaction)
        account_line = next(
            (line for line in transaction.lines if line.account_id in visible_ids), None
        )
        display_payee, display_note = _display_text(db, member, transaction)
        readable.write_datetime(
            row, 0, transaction.occurred_at.astimezone(SHANGHAI).replace(tzinfo=None), date_fmt
        )
        readable.write(row, 1, display_payee)
        readable.write(row, 2, type_names[transaction.type])
        readable.write(
            row,
            3,
            categories.get(transaction.category_id, "待分类")
            if transaction.type != "transfer"
            else "—",
        )
        readable.write_string(row, 4, _minor_text(transaction.amount_minor), money_text)
        readable.write(row, 5, transaction.currency)
        readable.write(
            row,
            6,
            _account_name(db, member, account_line.account_id if account_line else None, None),
        )
        readable.write(row, 7, display_note)

    if tier >= 2:
        detail = workbook.add_worksheet("交易明细")
        detail.freeze_panes(1, 0)
        detail.set_column("A:A", 38)
        detail.set_column("B:B", 18)
        detail.set_column("C:C", 28)
        detail.set_column("D:M", 16)
        detail.write_row(
            0,
            0,
            [
                "交易ID",
                "日期",
                "账面名称",
                "类型",
                "分类",
                "原币金额",
                "币种",
                "人民币金额",
                "汇率",
                "来源",
                "导入批次",
                "创建人",
                "备注",
            ],
            header,
        )
        for row, transaction in enumerate(transactions, start=1):
            display_payee, display_note = _display_text(db, member, transaction)
            detail.write(row, 0, transaction.id)
            detail.write_datetime(
                row, 1, transaction.occurred_at.astimezone(SHANGHAI).replace(tzinfo=None), date_fmt
            )
            detail.write(row, 2, display_payee)
            detail.write(row, 3, type_names[transaction.type])
            detail.write(row, 4, categories.get(transaction.category_id, "待分类"))
            detail.write_string(row, 5, _minor_text(transaction.amount_minor), money_text)
            detail.write(row, 6, transaction.currency)
            detail.write_string(row, 7, _minor_text(transaction.base_amount_minor), money_text)
            detail.write(
                row,
                8,
                format(transaction.fx_rate, "f") if transaction.fx_rate is not None else "",
            )
            detail.write(row, 9, "导入" if transaction.source == "import" else "手工")
            detail.write(
                row,
                10,
                ""
                if transaction_has_hidden_accounts(db, member, transaction)
                else transaction.batch_id or "",
            )
            detail.write(row, 11, transaction.created_by)
            detail.write(row, 12, display_note)

    if tier >= 3:
        journal = workbook.add_worksheet("复式分录")
        journal.freeze_panes(1, 0)
        journal.set_column("A:A", 38)
        journal.set_column("B:B", 18)
        journal.set_column("C:C", 28)
        journal.set_column("D:D", 24)
        journal.set_column("E:H", 16)
        journal.write_row(
            0,
            0,
            [
                "交易ID",
                "日期",
                "账面名称",
                "账户或科目",
                "借方（人民币）",
                "贷方（人民币）",
                "账户币种金额",
                "账户币种",
            ],
            header,
        )
        row = 1
        for transaction in transactions:
            display_payee, _display_note = _display_text(db, member, transaction)
            for line in transaction.lines:
                journal.write(row, 0, transaction.id)
                journal.write_datetime(
                    row,
                    1,
                    transaction.occurred_at.astimezone(SHANGHAI).replace(tzinfo=None),
                    date_fmt,
                )
                journal.write(row, 2, display_payee)
                journal.write(
                    row,
                    3,
                    _account_name(db, member, line.account_id, line.ledger_code),
                )
                journal.write_string(row, 4, _minor_text(line.debit_minor), money_text)
                journal.write_string(row, 5, _minor_text(line.credit_minor), money_text)
                if line.account_amount_minor is not None and line.account_id in accounts:
                    journal.write_string(row, 6, _minor_text(line.account_amount_minor), money_text)
                    journal.write(row, 7, accounts[line.account_id].currency)
                else:
                    journal.write_blank(row, 6, None)
                    journal.write_blank(row, 7, None)
                row += 1
        journal.write(row + 1, 3, "合计", header)
        debit_total = sum(line.debit_minor for item in transactions for line in item.lines)
        credit_total = sum(line.credit_minor for item in transactions for line in item.lines)
        journal.write_string(row + 1, 4, _minor_text(debit_total), money_text)
        journal.write_string(row + 1, 5, _minor_text(credit_total), money_text)

    if tier >= 4:
        reconcile = workbook.add_worksheet("核对汇总")
        reconcile.set_column("A:A", 26)
        reconcile.set_column("B:F", 17)
        reconcile.write_row(0, 0, ["账户", "期初", "增加", "减少", "期末", "差额", "币种"], header)
        row = 1
        for account in accounts.values():
            increase = decrease = 0
            for transaction in transactions:
                for line in transaction.lines:
                    if line.account_id == account.id:
                        native_amount = line.account_amount_minor or 0
                        increases_balance = (
                            line.debit_minor > 0
                            if account.kind == "asset"
                            else line.credit_minor > 0
                        )
                        if increases_balance:
                            increase += native_amount
                        else:
                            decrease += native_amount
            if increase == 0 and decrease == 0:
                continue
            opening = _opening_balance_at(db, account, starts_at)
            close = opening + increase - decrease
            reconcile.write(row, 0, account.name)
            reconcile.write_string(row, 1, _minor_text(opening), money_text)
            reconcile.write_string(row, 2, _minor_text(increase), money_text)
            reconcile.write_string(row, 3, _minor_text(decrease), money_text)
            reconcile.write_string(row, 4, _minor_text(close), money_text)
            reconcile.write_string(
                row,
                5,
                _minor_text(close - (opening + increase - decrease)),
                money_text,
            )
            reconcile.write(row, 6, account.currency)
            row += 1
        reconcile.write(row + 1, 0, "全部分录借贷差额", header)
        reconcile.write_string(
            row + 1,
            1,
            _minor_text(
                sum(sum(line.debit_minor for line in t.lines) for t in transactions)
                - sum(sum(line.credit_minor for line in t.lines) for t in transactions)
            ),
            money_text,
        )

    if tier >= 5:
        stats = workbook.add_worksheet("统计分析")
        stats.set_column("A:A", 24)
        stats.set_column("B:C", 16)
        stats.set_column("Y:Z", None, None, {"hidden": True})
        stats.write(0, 0, "统计分析", title)
        stats.write(1, 0, f"范围：{starts_on.isoformat()} 至 {ends_on.isoformat()}", muted)
        stats.write(2, 0, "图表使用比例刻度；精确金额以左侧文本为准", muted)
        expense_total = sum(t.base_amount_minor for t in transactions if t.type == "expense") - sum(
            t.base_amount_minor for t in transactions if t.type == "refund"
        )
        income_total = sum(t.base_amount_minor for t in transactions if t.type == "income")
        stats.write_row(3, 0, ["指标", "金额"], header)
        stats.write(4, 0, "收入")
        stats.write_string(4, 1, _minor_text(income_total), money_text)
        stats.write(5, 0, "支出")
        stats.write_string(5, 1, _minor_text(expense_total), money_text)
        stats.write(6, 0, "净结余")
        stats.write_string(6, 1, _minor_text(income_total - expense_total), money_text)

        category_totals: dict[str, int] = defaultdict(int)
        month_income: dict[str, int] = defaultdict(int)
        month_expense: dict[str, int] = defaultdict(int)
        for transaction in transactions:
            month = transaction.occurred_at.astimezone(SHANGHAI).strftime("%Y-%m")
            if transaction.type == "expense":
                category_totals[categories.get(transaction.category_id, "待分类")] += (
                    transaction.base_amount_minor
                )
                month_expense[month] += transaction.base_amount_minor
            elif transaction.type == "refund":
                category_totals[categories.get(transaction.category_id, "待分类")] -= (
                    transaction.base_amount_minor
                )
                month_expense[month] -= transaction.base_amount_minor
            elif transaction.type == "income":
                month_income[month] += transaction.base_amount_minor
        top = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
        if len(top) > 6:
            top = top[:5] + [("其他", sum(value for _, value in top[5:]))]
        stats.write_row(9, 0, ["支出分类", "金额"], header)
        for index, (name, value) in enumerate(top, start=10):
            stats.write(index, 0, name)
            stats.write_string(index, 1, _minor_text(value), money_text)
        if top:
            for index, value in enumerate(
                _scaled_chart_values([max(value, 0) for _, value in top]), start=10
            ):
                stats.write_number(index, 24, value)
            donut = workbook.add_chart({"type": "doughnut"})
            donut.add_series(
                {
                    "name": "支出分类",
                    "categories": ["统计分析", 10, 0, 9 + len(top), 0],
                    "values": ["统计分析", 10, 24, 9 + len(top), 24],
                    "data_labels": {"percentage": True, "leader_lines": True},
                }
            )
            donut.set_title({"name": "支出分类（前五类及其他）"})
            donut.set_hole_size(55)
            stats.insert_chart("D4", donut, {"x_scale": 1.1, "y_scale": 1.1})

        months = sorted(set(month_income) | set(month_expense))
        start_row = 18
        stats.write_row(start_row, 0, ["月份", "收入", "支出"], header)
        for offset, month in enumerate(months, start=1):
            stats.write(start_row + offset, 0, month)
            stats.write_string(start_row + offset, 1, _minor_text(month_income[month]), money_text)
            stats.write_string(start_row + offset, 2, _minor_text(month_expense[month]), money_text)
        if months:
            chart_values = _scaled_chart_values(
                [month_income[month] for month in months]
                + [month_expense[month] for month in months]
            )
            for offset, value in enumerate(chart_values[: len(months)], start=1):
                stats.write_number(start_row + offset, 24, value)
            for offset, value in enumerate(chart_values[len(months) :], start=1):
                stats.write_number(start_row + offset, 25, value)
            trend = workbook.add_chart({"type": "column"})
            for column, name in ((24, "收入"), (25, "支出")):
                trend.add_series(
                    {
                        "name": name,
                        "categories": ["统计分析", start_row + 1, 0, start_row + len(months), 0],
                        "values": [
                            "统计分析",
                            start_row + 1,
                            column,
                            start_row + len(months),
                            column,
                        ],
                    }
                )
            trend.set_title({"name": "月度收支趋势"})
            stats.insert_chart("D21", trend, {"x_scale": 1.1, "y_scale": 1.1})

    workbook.set_properties(
        {
            "title": "HomeDatabase 家庭账本",
            "comments": "从权威数据库同一快照生成；阅读报告不可回导。",
        }
    )
    workbook.close()
    return output.getvalue()
