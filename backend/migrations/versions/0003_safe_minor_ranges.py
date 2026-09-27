"""constrain every money minor column to the JavaScript safe-integer range

Revision ID: 0003_safe_minor_ranges
Revises: 0002_import_fx_review
Create Date: 2026-08-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_safe_minor_ranges"
down_revision: str | None = "0002_import_fx_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_SAFE_MINOR = 9_007_199_254_740_991


def upgrade() -> None:
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_accounts_opening_balance_safe",
            f"opening_balance_minor BETWEEN -{MAX_SAFE_MINOR} AND {MAX_SAFE_MINOR}",
        )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_transactions_amount_safe",
            f"amount_minor <= {MAX_SAFE_MINOR}",
        )
        batch_op.create_check_constraint(
            "ck_transactions_base_amount_safe",
            f"base_amount_minor <= {MAX_SAFE_MINOR}",
        )

    with op.batch_alter_table("entry_lines", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_entry_lines_amounts_safe",
            f"debit_minor <= {MAX_SAFE_MINOR} AND credit_minor <= {MAX_SAFE_MINOR}",
        )
        batch_op.create_check_constraint(
            "ck_entry_lines_account_amount_safe",
            f"account_amount_minor IS NULL OR account_amount_minor <= {MAX_SAFE_MINOR}",
        )

    with op.batch_alter_table("import_candidates", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_import_candidates_amount_safe",
            f"amount_minor <= {MAX_SAFE_MINOR}",
        )
        batch_op.create_check_constraint(
            "ck_import_candidates_base_amount_safe",
            f"base_amount_minor IS NULL OR base_amount_minor <= {MAX_SAFE_MINOR}",
        )

    with op.batch_alter_table("split_shares", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_split_shares_amount_safe",
            f"share_minor <= {MAX_SAFE_MINOR}",
        )


def downgrade() -> None:
    with op.batch_alter_table("split_shares", schema=None) as batch_op:
        batch_op.drop_constraint("ck_split_shares_amount_safe", type_="check")

    with op.batch_alter_table("import_candidates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_import_candidates_base_amount_safe", type_="check")
        batch_op.drop_constraint("ck_import_candidates_amount_safe", type_="check")

    with op.batch_alter_table("entry_lines", schema=None) as batch_op:
        batch_op.drop_constraint("ck_entry_lines_account_amount_safe", type_="check")
        batch_op.drop_constraint("ck_entry_lines_amounts_safe", type_="check")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_transactions_base_amount_safe", type_="check")
        batch_op.drop_constraint("ck_transactions_amount_safe", type_="check")

    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_constraint("ck_accounts_opening_balance_safe", type_="check")
