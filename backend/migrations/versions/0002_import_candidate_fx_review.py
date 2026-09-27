"""add foreign-currency review fields to import candidates

Revision ID: 0002_import_fx_review
Revises: 0001_initial
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_import_fx_review"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("import_candidates", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fx_rate", sa.Numeric(20, 10), nullable=True))
        batch_op.add_column(sa.Column("base_amount_minor", sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            "ck_import_candidates_fx_rate",
            "fx_rate IS NULL OR fx_rate > 0",
        )
        batch_op.create_check_constraint(
            "ck_import_candidates_base_amount",
            "base_amount_minor IS NULL OR base_amount_minor > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("import_candidates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_import_candidates_base_amount", type_="check")
        batch_op.drop_constraint("ck_import_candidates_fx_rate", type_="check")
        batch_op.drop_column("base_amount_minor")
        batch_op.drop_column("fx_rate")
