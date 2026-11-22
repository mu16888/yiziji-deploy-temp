"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-11-22 16:47:34.475562

"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Initial schema: all 17 tables from design §6.
    op.create_table('products',
    sa.Column('sku', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('amount_fen', sa.Integer(), nullable=False),
    sa.Column('original_amount_fen', sa.Integer(), nullable=True),
    sa.Column('credits', sa.Integer(), nullable=False),
    sa.Column('bonuses', sa.JSON(), nullable=True),
    sa.Column('eligibility', sa.JSON(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_sku'), ['sku'], unique=True)
        batch_op.create_index(batch_op.f('ix_products_type'), ['type'], unique=False)

    op.create_table('user_roles',
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('users',
    sa.Column('phone', sa.String(), nullable=True),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('password_hash', sa.String(), nullable=True),
    sa.Column('wechat_openid', sa.String(), nullable=True),
    sa.Column('display_name', sa.String(), nullable=True),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('identity_hash', sa.String(), nullable=True),
    sa.Column('org_id', sa.String(), nullable=True),
    sa.Column('invite_code', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_identity_hash'), ['identity_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_invite_code'), ['invite_code'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_phone'), ['phone'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_role'), ['role'], unique=False)
        batch_op.create_index(batch_op.f('ix_users_wechat_openid'), ['wechat_openid'], unique=True)

    op.create_table('agents',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('admission_order_no', sa.String(), nullable=True),
    sa.Column('fund_fen', sa.Integer(), nullable=False),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agents_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_agents_user_id'), ['user_id'], unique=True)

    op.create_table('audit_logs',
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('target_type', sa.String(), nullable=True),
    sa.Column('target_id', sa.String(), nullable=True),
    sa.Column('before', sa.JSON(), nullable=True),
    sa.Column('after', sa.JSON(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('ip', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_logs_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_logs_actor_id'), ['actor_id'], unique=False)

    op.create_table('credit_balance',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('pool', sa.String(), nullable=False),
    sa.Column('balance', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'pool', name='uq_balance_user_pool')
    )
    with op.batch_alter_table('credit_balance', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_balance_pool'), ['pool'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_balance_user_id'), ['user_id'], unique=False)

    op.create_table('credit_ledger',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('delta', sa.Integer(), nullable=False),
    sa.Column('balance_after', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('ref_type', sa.String(), nullable=True),
    sa.Column('ref_id', sa.String(), nullable=True),
    sa.Column('pool', sa.String(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('reason', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('credit_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_ledger_pool'), ['pool'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_ledger_type'), ['type'], unique=False)
        batch_op.create_index(batch_op.f('ix_credit_ledger_user_id'), ['user_id'], unique=False)

    op.create_table('generation_jobs',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('external_id', sa.String(), nullable=True),
    sa.Column('reserved_credits', sa.Integer(), nullable=True),
    sa.Column('actual_credits', sa.Integer(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('generation_jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_generation_jobs_external_id'), ['external_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_generation_jobs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_generation_jobs_user_id'), ['user_id'], unique=False)

    op.create_table('invitations',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(), nullable=False),
    sa.Column('channel', sa.String(), nullable=True),
    sa.Column('click_count', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_invitations_code'), ['code'], unique=True)
        batch_op.create_index(batch_op.f('ix_invitations_user_id'), ['user_id'], unique=False)

    op.create_table('orders',
    sa.Column('no', sa.String(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=True),
    sa.Column('sku', sa.String(), nullable=False),
    sa.Column('amount_fen', sa.Integer(), nullable=False),
    sa.Column('paid_amount_fen', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('channel', sa.String(), nullable=True),
    sa.Column('refund_status', sa.String(), nullable=True),
    sa.Column('paid_at', sa.DateTime(), nullable=True),
    sa.Column('fulfilled_at', sa.DateTime(), nullable=True),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_no'), ['no'], unique=True)
        batch_op.create_index(batch_op.f('ix_orders_sku'), ['sku'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_user_id'), ['user_id'], unique=False)

    op.create_table('promoters',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(), nullable=False),
    sa.Column('code', sa.String(), nullable=True),
    sa.Column('invited_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('promoters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_promoters_code'), ['code'], unique=True)
        batch_op.create_index(batch_op.f('ix_promoters_level'), ['level'], unique=False)
        batch_op.create_index(batch_op.f('ix_promoters_user_id'), ['user_id'], unique=True)

    op.create_table('withdrawals',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('amount_fen', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('reviewed_by', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('remark', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('withdrawals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_withdrawals_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_withdrawals_user_id'), ['user_id'], unique=False)

    op.create_table('agent_fund_ledger',
    sa.Column('agent_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('delta_fen', sa.Integer(), nullable=False),
    sa.Column('balance_after_fen', sa.Integer(), nullable=False),
    sa.Column('ref_type', sa.String(), nullable=True),
    sa.Column('ref_id', sa.String(), nullable=True),
    sa.Column('remark', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('agent_fund_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_fund_ledger_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agent_fund_ledger_user_id'), ['user_id'], unique=False)

    op.create_table('aigc_audit',
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('aigc_label', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['generation_jobs.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('aigc_audit', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_aigc_audit_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_aigc_audit_user_id'), ['user_id'], unique=False)

    op.create_table('attributions',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('inviter_id', sa.Integer(), nullable=True),
    sa.Column('code', sa.String(), nullable=True),
    sa.Column('candidate_at', sa.DateTime(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('promoter_id', sa.Integer(), nullable=True),
    sa.Column('agent_id', sa.Integer(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
    sa.ForeignKeyConstraint(['inviter_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['promoter_id'], ['promoters.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('attributions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attributions_inviter_id'), ['inviter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_attributions_user_id'), ['user_id'], unique=True)

    op.create_table('commission_ledger',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('order_no', sa.String(), nullable=True),
    sa.Column('promoter_id', sa.Integer(), nullable=True),
    sa.Column('agent_id', sa.Integer(), nullable=True),
    sa.Column('amount_fen', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('available_at', sa.DateTime(), nullable=True),
    sa.Column('reversed_at', sa.DateTime(), nullable=True),
    sa.Column('ref_type', sa.String(), nullable=True),
    sa.Column('ref_id', sa.String(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ),
    sa.ForeignKeyConstraint(['promoter_id'], ['promoters.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    with op.batch_alter_table('commission_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_commission_ledger_order_no'), ['order_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_commission_ledger_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_commission_ledger_user_id'), ['user_id'], unique=False)

    op.create_table('order_events',
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('order_no', sa.String(), nullable=False),
    sa.Column('event_id', sa.String(), nullable=True),
    sa.Column('event_type', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key'),
    sa.UniqueConstraint('order_no', 'event_id', name='uq_order_event')
    )
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_order_events_order_id'), ['order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_order_events_order_no'), ['order_no'], unique=False)



def downgrade() -> None:
    # Drop in reverse dependency order.
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_order_events_order_no'))
        batch_op.drop_index(batch_op.f('ix_order_events_order_id'))

    op.drop_table('order_events')
    with op.batch_alter_table('commission_ledger', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_commission_ledger_user_id'))
        batch_op.drop_index(batch_op.f('ix_commission_ledger_status'))
        batch_op.drop_index(batch_op.f('ix_commission_ledger_order_no'))

    op.drop_table('commission_ledger')
    with op.batch_alter_table('attributions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attributions_user_id'))
        batch_op.drop_index(batch_op.f('ix_attributions_inviter_id'))

    op.drop_table('attributions')
    with op.batch_alter_table('aigc_audit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_aigc_audit_user_id'))
        batch_op.drop_index(batch_op.f('ix_aigc_audit_job_id'))

    op.drop_table('aigc_audit')
    with op.batch_alter_table('agent_fund_ledger', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agent_fund_ledger_user_id'))
        batch_op.drop_index(batch_op.f('ix_agent_fund_ledger_agent_id'))

    op.drop_table('agent_fund_ledger')
    with op.batch_alter_table('withdrawals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_withdrawals_user_id'))
        batch_op.drop_index(batch_op.f('ix_withdrawals_status'))

    op.drop_table('withdrawals')
    with op.batch_alter_table('promoters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_promoters_user_id'))
        batch_op.drop_index(batch_op.f('ix_promoters_level'))
        batch_op.drop_index(batch_op.f('ix_promoters_code'))

    op.drop_table('promoters')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_user_id'))
        batch_op.drop_index(batch_op.f('ix_orders_status'))
        batch_op.drop_index(batch_op.f('ix_orders_sku'))
        batch_op.drop_index(batch_op.f('ix_orders_no'))

    op.drop_table('orders')
    with op.batch_alter_table('invitations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_invitations_user_id'))
        batch_op.drop_index(batch_op.f('ix_invitations_code'))

    op.drop_table('invitations')
    with op.batch_alter_table('generation_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_generation_jobs_user_id'))
        batch_op.drop_index(batch_op.f('ix_generation_jobs_status'))
        batch_op.drop_index(batch_op.f('ix_generation_jobs_external_id'))

    op.drop_table('generation_jobs')
    with op.batch_alter_table('credit_ledger', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_credit_ledger_user_id'))
        batch_op.drop_index(batch_op.f('ix_credit_ledger_type'))
        batch_op.drop_index(batch_op.f('ix_credit_ledger_pool'))

    op.drop_table('credit_ledger')
    with op.batch_alter_table('credit_balance', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_credit_balance_user_id'))
        batch_op.drop_index(batch_op.f('ix_credit_balance_pool'))

    op.drop_table('credit_balance')
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_logs_actor_id'))
        batch_op.drop_index(batch_op.f('ix_audit_logs_action'))

    op.drop_table('audit_logs')
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agents_user_id'))
        batch_op.drop_index(batch_op.f('ix_agents_status'))

    op.drop_table('agents')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_wechat_openid'))
        batch_op.drop_index(batch_op.f('ix_users_role'))
        batch_op.drop_index(batch_op.f('ix_users_phone'))
        batch_op.drop_index(batch_op.f('ix_users_org_id'))
        batch_op.drop_index(batch_op.f('ix_users_invite_code'))
        batch_op.drop_index(batch_op.f('ix_users_identity_hash'))

    op.drop_table('users')
    op.drop_table('user_roles')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_type'))
        batch_op.drop_index(batch_op.f('ix_products_sku'))

    op.drop_table('products')
