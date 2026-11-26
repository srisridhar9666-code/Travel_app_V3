"""add saved_hotels tenant_settings and telemetry_unlock

Revision ID: a5b8c9d1e2f3
Revises: e738c6b2910d
Create Date: 2026-11-23 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'a5b8c9d1e2f3'
down_revision: Union[str, None] = 'e738c6b2910d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    # 1. saved_hotels table
    if 'saved_hotels' not in tables:
        op.create_table(
            'saved_hotels',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('tenant_id', sa.String(length=50), nullable=False),
            sa.Column('vendor_id', sa.Integer(), sa.ForeignKey('vendors.id', ondelete='SET NULL'), nullable=True),
            sa.Column('hotel_name', sa.String(length=160), nullable=False),
            sa.Column('city', sa.String(length=120), nullable=False),
            sa.Column('state', sa.String(length=80), nullable=True),
            sa.Column('address', sa.String(length=255), nullable=False),
            sa.Column('landmark', sa.String(length=160), nullable=True),
            sa.Column('google_maps_url', sa.String(length=500), nullable=True),
            sa.Column('room_category', sa.String(length=80), nullable=False, server_default='Standard'),
            sa.Column('nightly_rate', sa.Numeric(precision=12, scale=2), nullable=False, server_default='0.00'),
            sa.Column('contact_phone', sa.String(length=32), nullable=True),
            sa.Column('amenities', mysql.JSON(), nullable=True),
            sa.Column('vendor_notes', sa.String(length=500), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_saved_hotels_tenant_id'), 'saved_hotels', ['tenant_id'], unique=False)
        op.create_index('ix_saved_hotels_tenant_city', 'saved_hotels', ['tenant_id', 'city'], unique=False)
        op.create_index('ix_saved_hotels_vendor', 'saved_hotels', ['tenant_id', 'vendor_id'], unique=False)

    # 2. tenant_settings table
    if 'tenant_settings' not in tables:
        op.create_table(
            'tenant_settings',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('tenant_id', sa.String(length=50), nullable=False),
            sa.Column('require_telemetry_checkout', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('max_monthly_missed_photos', sa.Integer(), nullable=False, server_default='5'),
            sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.Column('updated_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('tenant_id', name='uq_tenant_settings_tenant_id'),
        )

    # 3. users.telemetry_unlocked_at
    user_cols = [c['name'] for c in insp.get_columns('users')]
    if 'telemetry_unlocked_at' not in user_cols:
        op.add_column('users', sa.Column('telemetry_unlocked_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = insp.get_table_names()

    user_cols = [c['name'] for c in insp.get_columns('users')]
    if 'telemetry_unlocked_at' in user_cols:
        op.drop_column('users', 'telemetry_unlocked_at')

    if 'tenant_settings' in tables:
        op.drop_table('tenant_settings')

    if 'saved_hotels' in tables:
        op.drop_table('saved_hotels')
