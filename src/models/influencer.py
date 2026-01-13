from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base

if TYPE_CHECKING:
    from src.models.conversation import Conversation


class InfluencerStatus(str, enum.Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLACKLISTED = "blacklisted"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Influencer(Base):
    """Influencer entity synced from HubSpot."""

    __tablename__ = "influencers"

    hubspot_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    status: Mapped[InfluencerStatus] = mapped_column(
        Enum(InfluencerStatus),
        default=InfluencerStatus.PROSPECT,
        nullable=False,
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel),
        default=RiskLevel.LOW,
        nullable=False,
    )

    # Social handles
    instagram_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    twitter_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tiktok_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Metrics
    follower_count: Mapped[int | None] = mapped_column(nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(nullable=True)

    # Notes
    notes: Mapped[str | None] = mapped_column(nullable=True)

    # Relationships
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation",
        back_populates="influencer",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Influencer(id={self.id}, name={self.name}, email={self.email})>"
