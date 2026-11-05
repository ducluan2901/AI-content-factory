"""source_job_id: a job that is the translation of an approved job (same clips, new voice and captions)

Revision ID: c4e1a7d20b55
Revises: b7d2e9a4c3f1
Create Date: 2026-11-05 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e1a7d20b55'
down_revision: Union[str, Sequence[str], None] = 'b7d2e9a4c3f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_job_id', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('content_job', schema=None) as batch_op:
        batch_op.drop_column('source_job_id')
