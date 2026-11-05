"""brand columns: closing card with the client's name and logo

Revision ID: b7d2e9a4c3f1
Revises: 8c0fc3b2f01c
Create Date: 2026-11-05 09:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e9a4c3f1'
down_revision: Union[str, Sequence[str], None] = '8c0fc3b2f01c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('brand_name', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('brand_logo_path', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.drop_column('brand_logo_path')
        batch_op.drop_column('brand_name')
