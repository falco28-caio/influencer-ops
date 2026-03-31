import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.influencer import Influencer
    from src.models.message import Message
    from src.models.task import Task


class Channel(str, enum.Enum):
    EMAIL = "email"
    INSTAGRAM_DM = "instagram_dm"
    TWITTER_DM = "twitter_dm"
    TIKTOK_DM = "tiktok_dm"
    SLACK = "slack"


class ConversationStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    NEEDS_ACTION = "needs_action"
    WAITING_REPLY = "waiting_reply"
    SNOOZED = "snoozed"


class Conversation(Base):
    """A conversation thread with an influencer."""

    __tablename__ = "conversations"

    influencer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("influencers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel),
        default=Channel.EMAIL,
        nullable=False,
    )
    thread_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus),
        default=ConversationStatus.OPEN,
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Intent classification
    primary_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    influencer: Mapped["Influencer"] = relationship(
        "Influencer",
        back_populates="conversations",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        lazy="selectin",
        order_by="Message.timestamp",
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task",
        back_populates="conversation",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, channel={self.channel}, status={self.status})>"
