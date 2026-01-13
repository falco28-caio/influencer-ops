import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, List, Dict

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.audit_log import AuditLog
    from src.models.conversation import Conversation


class TaskType(str, enum.Enum):
    DRAFT_REPLY = "draft_reply"
    SCHEDULE_MEETING = "schedule_meeting"
    REMIND_PENDING = "remind_pending"
    PAYMENT_INFO = "payment_info"
    FOLLOW_UP = "follow_up"
    INITIAL_OUTREACH = "initial_outreach"
    CUSTOM = "custom"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(Base):
    """A unit of work for the agent."""

    __tablename__ = "tasks"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[TaskType] = mapped_column(
        Enum(TaskType),
        nullable=False,
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus),
        default=TaskStatus.PENDING,
        nullable=False,
    )

    # Draft content
    draft_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_subject: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Approval tracking
    approval_user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    approval_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Slack integration
    slack_message_ts: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    slack_channel_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Agent metadata
    confidence_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    intent_detected: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    context_used: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="tasks",
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="task",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, type={self.type}, status={self.status})>"
