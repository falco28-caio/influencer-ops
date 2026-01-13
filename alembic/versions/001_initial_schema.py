"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create influencers table
    op.create_table(
        "influencers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("hubspot_id", sa.String(255), unique=True, nullable=True, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column(
            "status",
            sa.Enum("prospect", "active", "inactive", "blacklisted", name="influencerstatus"),
            nullable=False,
            server_default="prospect",
        ),
        sa.Column(
            "risk_level",
            sa.Enum("low", "medium", "high", name="risklevel"),
            nullable=False,
            server_default="low",
        ),
        sa.Column("instagram_handle", sa.String(255), nullable=True),
        sa.Column("twitter_handle", sa.String(255), nullable=True),
        sa.Column("tiktok_handle", sa.String(255), nullable=True),
        sa.Column("youtube_channel", sa.String(255), nullable=True),
        sa.Column("follower_count", sa.Integer, nullable=True),
        sa.Column("engagement_rate", sa.Float, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create conversations table
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "influencer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("influencers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel",
            sa.Enum("email", "instagram_dm", "twitter_dm", "tiktok_dm", "slack", name="channel"),
            nullable=False,
            server_default="email",
        ),
        sa.Column("thread_id", sa.String(255), nullable=True, index=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open", "closed", "needs_action", "waiting_reply", "snoozed",
                name="conversationstatus"
            ),
            nullable=False,
            server_default="open",
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_intent", sa.String(100), nullable=True),
        sa.Column("sentiment", sa.String(50), nullable=True),
        sa.Column("priority", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create messages table
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "direction",
            sa.Enum("inbound", "outbound", name="messagedirection"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_masked", sa.Text, nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True, index=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("sender", sa.String(255), nullable=True),
        sa.Column("recipients", postgresql.JSON, nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create tasks table
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "type",
            sa.Enum(
                "draft_reply", "schedule_meeting", "remind_pending", "payment_info",
                "follow_up", "initial_outreach", "custom",
                name="tasktype"
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "in_progress", "awaiting_approval", "approved",
                "rejected", "sent", "failed", "cancelled",
                name="taskstatus"
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("draft_content", sa.Text, nullable=True),
        sa.Column("draft_subject", sa.String(500), nullable=True),
        sa.Column("approval_user_id", sa.String(100), nullable=True),
        sa.Column("approval_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("slack_message_ts", sa.String(100), nullable=True),
        sa.Column("slack_channel_id", sa.String(100), nullable=True),
        sa.Column("confidence_score", sa.Float, nullable=True),
        sa.Column("intent_detected", sa.String(100), nullable=True),
        sa.Column("context_used", postgresql.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("action", sa.String(100), nullable=False, index=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=True),
        sa.Column("llm_input", sa.Text, nullable=True),
        sa.Column("llm_output", sa.Text, nullable=True),
        sa.Column("llm_model", sa.String(100), nullable=True),
        sa.Column("llm_tokens_used", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("tasks")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("influencers")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute("DROP TYPE IF EXISTS tasktype")
    op.execute("DROP TYPE IF EXISTS messagedirection")
    op.execute("DROP TYPE IF EXISTS conversationstatus")
    op.execute("DROP TYPE IF EXISTS channel")
    op.execute("DROP TYPE IF EXISTS risklevel")
    op.execute("DROP TYPE IF EXISTS influencerstatus")
