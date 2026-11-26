"""invoice verification fields

Revision ID: e738c6b2910d
Revises: 22b1d24e229a
Create Date: 2026-11-23 14:35:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e738c6b2910d'
down_revision = '22b1d24e229a'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = [c['name'] for c in insp.get_columns('invoices')]
    if 'verified_by_id' not in cols:
        op.add_column('invoices', sa.Column('verified_by_id', sa.Integer(), nullable=True))
        op.create_foreign_key('fk_invoices_verified_by', 'invoices', 'users', ['verified_by_id'], ['id'], ondelete='SET NULL')
    if 'verified_at' not in cols:
        op.add_column('invoices', sa.Column('verified_at', sa.DateTime(), nullable=True))
    if 'verification_comment' not in cols:
        op.add_column('invoices', sa.Column('verification_comment', sa.String(length=500), nullable=True))


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = [c['name'] for c in insp.get_columns('invoices')]
    if 'verification_comment' in cols:
        op.drop_column('invoices', 'verification_comment')
    if 'verified_at' in cols:
        op.drop_column('invoices', 'verified_at')
    if 'verified_by_id' in cols:
        op.drop_constraint('fk_invoices_verified_by', 'invoices', type_='foreignkey')
        op.drop_column('invoices', 'verified_by_id')
