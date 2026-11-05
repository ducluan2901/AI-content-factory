"""visual_queries_json: operator overrides of a scene's stock search terms

Revision ID: d8f3b1c72a90
Revises: c4e1a7d20b55
Create Date: 2026-11-05 13:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f3b1c72a90'
down_revision: Union[str, Sequence[str], None] = 'c4e1a7d20b55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('visual_queries_json', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.drop_column('visual_queries_json')
