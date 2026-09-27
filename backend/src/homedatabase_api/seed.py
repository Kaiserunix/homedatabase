from __future__ import annotations

import argparse
import getpass
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import create_schema, get_session_factory
from .models import Account, AccountGrant, Category, Member
from .security import hash_password

MEMBERS = [
    ("m-dad", "dad", "爸爸", "dad", "bg-slate-600", False),
    ("m-mom", "mom", "妈妈", "mom", "bg-amber-500", False),
    ("m-son", "son", "儿子", "son", "bg-teal-600", True),
]

CATEGORIES = [
    ("c-food", "餐饮", "expense", "utensils"),
    ("c-grocery", "买菜超市", "expense", "shopping-cart"),
    ("c-transport", "交通", "expense", "train-front"),
    ("c-home", "居家", "expense", "sofa"),
    ("c-medical", "医疗", "expense", "heart-pulse"),
    ("c-education", "教育", "expense", "book-open"),
    ("c-fun", "娱乐", "expense", "gamepad-2"),
    ("c-other", "其他", "expense", "shapes"),
    ("c-salary", "工资", "income", "briefcase"),
    ("c-bonus", "奖金", "income", "gift"),
    ("c-interest", "理财收益", "income", "trending-up"),
    ("c-other-in", "其他收入", "income", "shapes"),
]

ACCOUNTS = [
    ("a-dad-bank", "爸爸银行卡", "asset", "personal", "m-dad", "CNY", 0, "landmark"),
    ("a-mom-bank", "妈妈银行卡", "asset", "personal", "m-mom", "CNY", 0, "landmark"),
    ("a-son-wallet", "儿子零钱", "asset", "personal", "m-son", "CNY", 0, "wallet"),
    ("a-family", "家庭公共账户", "asset", "family", None, "CNY", 0, "users"),
    ("a-cash", "家庭现金", "asset", "family", None, "CNY", 0, "banknote"),
    ("a-credit", "家庭信用卡", "liability", "family", None, "CNY", 0, "credit-card"),
]


def seed_family_data(db: Session, *, passwords: dict[str, str]) -> bool:
    if db.scalar(select(Member.id).limit(1)) is not None:
        return False
    for member_id, identity, name, username, color, is_admin in MEMBERS:
        db.add(
            Member(
                id=member_id,
                identity=identity,
                name=name,
                username=username,
                password_hash=hash_password(passwords[identity]),
                color=color,
                is_admin=is_admin,
            )
        )
    # These models intentionally do not expose broad ORM relationships. Flush the
    # referenced rows explicitly so SQLite/PostgreSQL both honour the foreign keys.
    db.flush()
    for category_id, name, kind, icon in CATEGORIES:
        db.add(Category(id=category_id, name=name, kind=kind, icon=icon))
    for account_id, name, kind, scope, owner_id, currency, balance, icon in ACCOUNTS:
        db.add(
            Account(
                id=account_id,
                name=name,
                kind=kind,
                scope=scope,
                owner_id=owner_id,
                currency=currency,
                opening_balance_minor=balance,
                icon=icon,
            )
        )
    db.flush()
    for account_id in ("a-family", "a-cash", "a-credit"):
        for member_id, *_rest in MEMBERS:
            db.add(AccountGrant(account_id=account_id, member_id=member_id, permission="manage"))
    db.commit()
    return True


def seed_demo_data(db: Session, *, password: str) -> None:
    seed_family_data(
        db,
        passwords={identity: password for _id, identity, *_rest in MEMBERS},
    )


def _prompt_distinct_passwords() -> dict[str, str]:
    passwords: dict[str, str] = {}
    labels = {"dad": "爸爸", "mom": "妈妈", "son": "儿子"}
    for identity in ("dad", "mom", "son"):
        first = getpass.getpass(f"请输入{labels[identity]}的初始密码（至少 12 位）：")
        second = getpass.getpass("请再次输入：")
        if first != second:
            raise SystemExit("两次密码不一致，未写入任何数据")
        if len(first) < 12:
            raise SystemExit("生产初始密码至少需要 12 个字符")
        if first in passwords.values():
            raise SystemExit("三名成员必须使用不同的初始密码")
        passwords[identity] = first
    return passwords


def run() -> None:
    parser = argparse.ArgumentParser(description="Initialize the fixed HomeDatabase family")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--demo", action="store_true", help="create the fixed family and starter data"
    )
    mode.add_argument(
        "--bootstrap-family",
        action="store_true",
        help="interactively initialize production with three distinct passwords",
    )
    parser.add_argument("--password", help="initial password for all three local demo users")
    args = parser.parse_args()
    settings = get_settings()
    if args.demo:
        if settings.environment == "production":
            parser.error("--demo is forbidden in production; use --bootstrap-family")
        password = args.password or secrets.token_urlsafe(15)
        create_schema()
        with get_session_factory()() as db:
            created = seed_family_data(
                db,
                passwords={identity: password for _id, identity, *_rest in MEMBERS},
            )
        if not created:
            parser.error("database already contains family members")
        if args.password is None:
            print(f"Local demo users created. One-time initial password: {password}")
        return
    if args.password:
        parser.error("--password is only valid with --demo")
    passwords = _prompt_distinct_passwords()
    with get_session_factory()() as db:
        if not seed_family_data(db, passwords=passwords):
            parser.error("database already contains family members")
    print("Production family initialized. Register Passkeys after the first password login.")
