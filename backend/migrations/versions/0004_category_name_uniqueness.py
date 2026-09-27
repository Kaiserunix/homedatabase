"""make sibling category names unique when parent_id is null

Revision ID: 0004_category_name_uniqueness
Revises: 0003_safe_minor_ranges
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_category_name_uniqueness"
down_revision: str | None = "0003_safe_minor_ranges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_constraint("uq_categories_sibling_name", type_="unique")
    op.create_index(
        "uq_categories_sibling_name",
        "categories",
        ["kind", sa.text("COALESCE(parent_id, '')"), "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_categories_sibling_name", table_name="categories")
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_categories_sibling_name",
            ["kind", "parent_id", "name"],
        )
