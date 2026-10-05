"""trip extensions, one booking email for several people, invoice payment

Revision ID: f3b8d1a6c274
Revises: e4a8c2f6b913
Create Date: 2026-10-05 18:00:00.000000

* travel_requests.extends_request_id - a cab kept more days or a stay made
  longer is a request of its own, linked to the trip it carries on.
* notifications.attachments - a booking email can carry several files; and
  to_address widens to 500 so one message can go to everyone booked together.
* invoices.paid_on / payment_reference / paid_by_id / paid_at - an approved
  invoice is either still to be paid or paid on a given day.

Checks first, so an interrupted run can be repeated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'f3b8d1a6c274'
down_revision: Union[str, None] = 'e4a8c2f6b913'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: DATETIME(6) on MySQL, as UTCDateTime is everywhere else.
_STAMP = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')


def _columns(table: str) -> dict[str, dict]:
    return {c['name']: c for c in sa.inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    return {fk['name'] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _indexes(table: str) -> set[str]:
    return {ix['name'] for ix in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    # --- extensions ----------------------------------------------------------
    if 'extends_request_id' not in _columns('travel_requests'):
        op.add_column(
            'travel_requests', sa.Column('extends_request_id', sa.Integer(), nullable=True)
        )
    if 'ix_travel_requests_extends_request_id' not in _indexes('travel_requests'):
        op.create_index(
            'ix_travel_requests_extends_request_id', 'travel_requests', ['extends_request_id']
        )
    if 'fk_travel_requests_extends' not in _foreign_keys('travel_requests'):
        op.create_foreign_key(
            'fk_travel_requests_extends', 'travel_requests', 'travel_requests',
            ['extends_request_id'], ['id'], ondelete='SET NULL',
        )

    # --- one email, several people and several files ------------------------
    notification_columns = _columns('notifications')
    if 'attachments' not in notification_columns:
        op.add_column('notifications', sa.Column('attachments', sa.JSON(), nullable=True))
    to_address = notification_columns.get('to_address')
    if to_address is not None and getattr(to_address['type'], 'length', 500) < 500:
        op.alter_column(
            'notifications', 'to_address',
            existing_type=sa.String(255), type_=sa.String(500), existing_nullable=True,
        )

    # --- invoice payment ------------------------------------------------------
    invoice_columns = _columns('invoices')
    for name, kind in (
        ('paid_on', sa.Date()),
        ('payment_reference', sa.String(80)),
        ('paid_by_id', sa.Integer()),
        ('paid_at', _STAMP),
    ):
        if name not in invoice_columns:
            op.add_column('invoices', sa.Column(name, kind, nullable=True))
    if 'fk_invoices_paid_by' not in _foreign_keys('invoices'):
        op.create_foreign_key(
            'fk_invoices_paid_by', 'invoices', 'users', ['paid_by_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    op.drop_constraint('fk_invoices_paid_by', 'invoices', type_='foreignkey')
    for name in ('paid_at', 'paid_by_id', 'payment_reference', 'paid_on'):
        op.drop_column('invoices', name)

    op.alter_column(
        'notifications', 'to_address',
        existing_type=sa.String(500), type_=sa.String(255), existing_nullable=True,
    )
    op.drop_column('notifications', 'attachments')

    op.drop_constraint('fk_travel_requests_extends', 'travel_requests', type_='foreignkey')
    op.drop_index('ix_travel_requests_extends_request_id', table_name='travel_requests')
    op.drop_column('travel_requests', 'extends_request_id')
