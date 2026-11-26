"""add telemetry location names and bug_reports table

Revision ID: b6c7d8e9f0a1
Revises: a5b8c9d1e2f3
Create Date: 2026-11-26 14:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b8c9d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    # 1. Location place names on cab_telemetries
    if 'cab_telemetries' in tables:
        cab_cols = [c['name'] for c in insp.get_columns('cab_telemetries')]
        if 'start_location_name' not in cab_cols:
            op.add_column('cab_telemetries', sa.Column('start_location_name', sa.String(length=255), nullable=True))
        if 'end_location_name' not in cab_cols:
            op.add_column('cab_telemetries', sa.Column('end_location_name', sa.String(length=255), nullable=True))

    # 2. Location place names on hotel_stay_verifications
    if 'hotel_stay_verifications' in tables:
        hotel_cols = [c['name'] for c in insp.get_columns('hotel_stay_verifications')]
        if 'check_in_location_name' not in hotel_cols:
            op.add_column('hotel_stay_verifications', sa.Column('check_in_location_name', sa.String(length=255), nullable=True))
        if 'check_out_location_name' not in hotel_cols:
            op.add_column('hotel_stay_verifications', sa.Column('check_out_location_name', sa.String(length=255), nullable=True))

    # 3. bug_reports table
    if 'bug_reports' not in tables:
        op.create_table(
            'bug_reports',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('tenant_id', sa.String(length=50), nullable=False),
            sa.Column('reporter_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_role', sa.String(length=40), nullable=True),
            sa.Column('page_url', sa.String(length=500), nullable=True),
            sa.Column('page_title', sa.String(length=200), nullable=True),
            sa.Column('browser_info', sa.String(length=500), nullable=True),
            sa.Column('comment', sa.Text(), nullable=False),
            sa.Column('screenshot_path', sa.String(length=500), nullable=True),
            sa.Column('status', sa.String(length=30), nullable=False, server_default='OPEN'),
            sa.Column('resolution_note', sa.Text(), nullable=True),
            sa.Column('resolved_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('resolved_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True),
            sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_bug_reports_tenant_id', 'bug_reports', ['tenant_id'], unique=False)
        op.create_index('ix_bug_reports_reporter_id', 'bug_reports', ['reporter_id'], unique=False)
        op.create_index('ix_bug_reports_tenant_status', 'bug_reports', ['tenant_id', 'status'], unique=False)
        op.create_index('ix_bug_reports_reporter', 'bug_reports', ['tenant_id', 'reporter_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    if 'bug_reports' in tables:
        op.drop_table('bug_reports')

    if 'hotel_stay_verifications' in tables:
        hotel_cols = [c['name'] for c in insp.get_columns('hotel_stay_verifications')]
        if 'check_out_location_name' in hotel_cols:
            op.drop_column('hotel_stay_verifications', 'check_out_location_name')
        if 'check_in_location_name' in hotel_cols:
            op.drop_column('hotel_stay_verifications', 'check_in_location_name')

    if 'cab_telemetries' in tables:
        cab_cols = [c['name'] for c in insp.get_columns('cab_telemetries')]
        if 'end_location_name' in cab_cols:
            op.drop_column('cab_telemetries', 'end_location_name')
        if 'start_location_name' in cab_cols:
            op.drop_column('cab_telemetries', 'start_location_name')
