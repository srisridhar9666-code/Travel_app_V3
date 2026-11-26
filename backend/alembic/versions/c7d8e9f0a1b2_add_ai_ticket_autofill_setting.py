"""add ai_ticket_autofill_enabled to tenant_settings

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-11-26 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'tenant_settings' in insp.get_table_names():
        cols = [c['name'] for c in insp.get_columns('tenant_settings')]
        if 'ai_ticket_autofill_enabled' not in cols:
            op.add_column(
                'tenant_settings',
                sa.Column(
                    'ai_ticket_autofill_enabled',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text('1'),
                ),
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if 'tenant_settings' in insp.get_table_names():
        cols = [c['name'] for c in insp.get_columns('tenant_settings')]
        if 'ai_ticket_autofill_enabled' in cols:
            op.drop_column('tenant_settings', 'ai_ticket_autofill_enabled')
