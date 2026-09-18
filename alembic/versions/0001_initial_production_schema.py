"""initial production schema

Creates the whole schema in one revision: users and email tokens, the per-user
contact and position trackers, the shared catalog plus its scraper bookkeeping,
plans / subscriptions / payments, the AI assistant tables, and the activity log.

This is the first Alembic revision. The pre-Alembic SQLite database is not
migrated in place - see the README for moving that data to Postgres.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('catalog_job_title',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('field', sa.String(length=120), nullable=True),
    sa.Column('level', sa.String(length=60), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('typical_requirements', sa.Text(), nullable=True),
    sa.Column('source_url', sa.String(length=500), nullable=True),
    sa.Column('is_published', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('title', 'field', name='uq_job_title_field')
    )
    op.create_index(op.f('ix_catalog_job_title_field'), 'catalog_job_title', ['field'], unique=False)
    op.create_index(op.f('ix_catalog_job_title_level'), 'catalog_job_title', ['level'], unique=False)
    op.create_index(op.f('ix_catalog_job_title_title'), 'catalog_job_title', ['title'], unique=False)
    op.create_table('catalog_university',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('website', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_catalog_university_country'), 'catalog_university', ['country'], unique=False)
    op.create_index(op.f('ix_catalog_university_slug'), 'catalog_university', ['slug'], unique=True)
    op.create_table('plan',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price_rial', sa.BigInteger(), nullable=False),
    sa.Column('duration_days', sa.Integer(), nullable=True),
    sa.Column('grants_catalog', sa.Boolean(), nullable=False),
    sa.Column('grants_ai', sa.Boolean(), nullable=False),
    sa.Column('ai_monthly_quota', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_plan_code'), 'plan', ['code'], unique=True)
    op.create_table('scrape_source',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('parser', sa.String(length=80), nullable=False),
    sa.Column('start_url', sa.String(length=500), nullable=False),
    sa.Column('university_slug', sa.String(length=120), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scrape_source_key'), 'scrape_source', ['key'], unique=True)
    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('is_email_verified', sa.Boolean(), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('signup_ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_email'), 'user', ['email'], unique=True)
    op.create_index(op.f('ix_user_username'), 'user', ['username'], unique=True)
    op.create_table('activity_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=60), nullable=True),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('summary', sa.String(length=500), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('path', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_activity_action_created', 'activity_log', ['action', 'created_at'], unique=False)
    op.create_index('ix_activity_created', 'activity_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_activity_log_created_at'), 'activity_log', ['created_at'], unique=False)
    op.create_index('ix_activity_user_created', 'activity_log', ['user_id', 'created_at'], unique=False)
    op.create_table('ai_usage',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.BigInteger(), nullable=False),
    sa.Column('output_tokens', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'period', name='uq_ai_usage_user_period')
    )
    op.create_index(op.f('ix_ai_usage_period'), 'ai_usage', ['period'], unique=False)
    op.create_index(op.f('ix_ai_usage_user_id'), 'ai_usage', ['user_id'], unique=False)
    op.create_table('catalog_professor',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('university_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('department', sa.String(length=200), nullable=True),
    sa.Column('research_focus', sa.Text(), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('profile_url', sa.String(length=500), nullable=True),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('source_key', sa.String(length=80), nullable=True),
    sa.Column('scraped_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_published', sa.Boolean(), nullable=False),
    sa.Column('is_removed', sa.Boolean(), nullable=False),
    sa.Column('removed_reason', sa.String(length=500), nullable=True),
    sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['university_id'], ['catalog_university.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('university_id', 'name', 'email', name='uq_catalog_prof_identity')
    )
    op.create_index('ix_catalog_prof_published', 'catalog_professor', ['is_published', 'is_removed'], unique=False)
    op.create_index(op.f('ix_catalog_professor_department'), 'catalog_professor', ['department'], unique=False)
    op.create_index(op.f('ix_catalog_professor_name'), 'catalog_professor', ['name'], unique=False)
    op.create_index(op.f('ix_catalog_professor_source_key'), 'catalog_professor', ['source_key'], unique=False)
    op.create_index(op.f('ix_catalog_professor_university_id'), 'catalog_professor', ['university_id'], unique=False)
    op.create_table('conversation',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversation_user_id'), 'conversation', ['user_id'], unique=False)
    op.create_index('ix_conversation_user_updated', 'conversation', ['user_id', 'updated_at'], unique=False)
    op.create_table('email_token',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_email_token_token_hash'), 'email_token', ['token_hash'], unique=True)
    op.create_index(op.f('ix_email_token_user_id'), 'email_token', ['user_id'], unique=False)
    op.create_index('ix_email_token_user_purpose', 'email_token', ['user_id', 'purpose'], unique=False)
    op.create_table('position',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('field', sa.String(length=300), nullable=False),
    sa.Column('link', sa.String(length=500), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('deadline', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_position_owner_category', 'position', ['owner_id', 'category'], unique=False)
    op.create_index(op.f('ix_position_owner_id'), 'position', ['owner_id'], unique=False)
    op.create_table('scrape_run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_key', sa.String(length=80), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('pages_fetched', sa.Integer(), nullable=False),
    sa.Column('records_found', sa.Integer(), nullable=False),
    sa.Column('records_created', sa.Integer(), nullable=False),
    sa.Column('records_updated', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('log', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('triggered_by_user_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['triggered_by_user_id'], ['user.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scrape_run_source_key'), 'scrape_run', ['source_key'], unique=False)
    op.create_table('subscription',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('granted_by_user_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['granted_by_user_id'], ['user.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_subscription_expires_at'), 'subscription', ['expires_at'], unique=False)
    op.create_index(op.f('ix_subscription_user_id'), 'subscription', ['user_id'], unique=False)
    op.create_index('ix_subscription_user_status', 'subscription', ['user_id', 'status'], unique=False)
    op.create_table('ai_message',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversation.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_message_conversation_id'), 'ai_message', ['conversation_id'], unique=False)
    op.create_table('contact',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('university', sa.String(length=200), nullable=False),
    sa.Column('research_focus', sa.String(length=500), nullable=False),
    sa.Column('contact_email', sa.String(length=320), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('email_sent', sa.Boolean(), nullable=False),
    sa.Column('email_sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reminder_sent', sa.Boolean(), nullable=False),
    sa.Column('catalog_professor_id', sa.Integer(), nullable=True),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['catalog_professor_id'], ['catalog_professor.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_contact_owner_category', 'contact', ['owner_id', 'category'], unique=False)
    op.create_index(op.f('ix_contact_owner_id'), 'contact', ['owner_id'], unique=False)
    op.create_index('ix_contact_owner_name', 'contact', ['owner_id', 'name'], unique=False)
    op.create_table('payment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=False),
    sa.Column('subscription_id', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('authority', sa.String(length=80), nullable=True),
    sa.Column('ref_id', sa.String(length=80), nullable=True),
    sa.Column('card_pan', sa.String(length=40), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('raw_request', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscription.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_authority'), 'payment', ['authority'], unique=True)
    op.create_index(op.f('ix_payment_ref_id'), 'payment', ['ref_id'], unique=False)
    op.create_index(op.f('ix_payment_user_id'), 'payment', ['user_id'], unique=False)
    op.create_index('ix_payment_user_status', 'payment', ['user_id', 'status'], unique=False)
    op.create_table('takedown_request',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('professor_id', sa.Integer(), nullable=True),
    sa.Column('requester_email', sa.String(length=320), nullable=True),
    sa.Column('subject_name', sa.String(length=200), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handled_by_user_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['handled_by_user_id'], ['user.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['professor_id'], ['catalog_professor.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('takedown_request')
    op.drop_index(op.f('ix_payment_authority'), table_name='payment')
    op.drop_index(op.f('ix_payment_ref_id'), table_name='payment')
    op.drop_index(op.f('ix_payment_user_id'), table_name='payment')
    op.drop_index('ix_payment_user_status', table_name='payment')
    op.drop_table('payment')
    op.drop_index('ix_contact_owner_category', table_name='contact')
    op.drop_index(op.f('ix_contact_owner_id'), table_name='contact')
    op.drop_index('ix_contact_owner_name', table_name='contact')
    op.drop_table('contact')
    op.drop_index(op.f('ix_ai_message_conversation_id'), table_name='ai_message')
    op.drop_table('ai_message')
    op.drop_index(op.f('ix_subscription_expires_at'), table_name='subscription')
    op.drop_index(op.f('ix_subscription_user_id'), table_name='subscription')
    op.drop_index('ix_subscription_user_status', table_name='subscription')
    op.drop_table('subscription')
    op.drop_index(op.f('ix_scrape_run_source_key'), table_name='scrape_run')
    op.drop_table('scrape_run')
    op.drop_index('ix_position_owner_category', table_name='position')
    op.drop_index(op.f('ix_position_owner_id'), table_name='position')
    op.drop_table('position')
    op.drop_index(op.f('ix_email_token_token_hash'), table_name='email_token')
    op.drop_index(op.f('ix_email_token_user_id'), table_name='email_token')
    op.drop_index('ix_email_token_user_purpose', table_name='email_token')
    op.drop_table('email_token')
    op.drop_index(op.f('ix_conversation_user_id'), table_name='conversation')
    op.drop_index('ix_conversation_user_updated', table_name='conversation')
    op.drop_table('conversation')
    op.drop_index('ix_catalog_prof_published', table_name='catalog_professor')
    op.drop_index(op.f('ix_catalog_professor_department'), table_name='catalog_professor')
    op.drop_index(op.f('ix_catalog_professor_name'), table_name='catalog_professor')
    op.drop_index(op.f('ix_catalog_professor_source_key'), table_name='catalog_professor')
    op.drop_index(op.f('ix_catalog_professor_university_id'), table_name='catalog_professor')
    op.drop_table('catalog_professor')
    op.drop_index(op.f('ix_ai_usage_period'), table_name='ai_usage')
    op.drop_index(op.f('ix_ai_usage_user_id'), table_name='ai_usage')
    op.drop_table('ai_usage')
    op.drop_index('ix_activity_action_created', table_name='activity_log')
    op.drop_index('ix_activity_created', table_name='activity_log')
    op.drop_index(op.f('ix_activity_log_created_at'), table_name='activity_log')
    op.drop_index('ix_activity_user_created', table_name='activity_log')
    op.drop_table('activity_log')
    op.drop_index(op.f('ix_user_email'), table_name='user')
    op.drop_index(op.f('ix_user_username'), table_name='user')
    op.drop_table('user')
    op.drop_index(op.f('ix_scrape_source_key'), table_name='scrape_source')
    op.drop_table('scrape_source')
    op.drop_index(op.f('ix_plan_code'), table_name='plan')
    op.drop_table('plan')
    op.drop_index(op.f('ix_catalog_university_country'), table_name='catalog_university')
    op.drop_index(op.f('ix_catalog_university_slug'), table_name='catalog_university')
    op.drop_table('catalog_university')
    op.drop_index(op.f('ix_catalog_job_title_field'), table_name='catalog_job_title')
    op.drop_index(op.f('ix_catalog_job_title_level'), table_name='catalog_job_title')
    op.drop_index(op.f('ix_catalog_job_title_title'), table_name='catalog_job_title')
    op.drop_table('catalog_job_title')
