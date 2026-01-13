from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# Enums matching database models
class InfluencerStatusEnum(str, Enum):
    PROSPECT = "prospect"
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLACKLISTED = "blacklisted"


class RiskLevelEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ChannelEnum(str, Enum):
    EMAIL = "email"
    INSTAGRAM_DM = "instagram_dm"
    TWITTER_DM = "twitter_dm"
    TIKTOK_DM = "tiktok_dm"
    SLACK = "slack"


class ConversationStatusEnum(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    NEEDS_ACTION = "needs_action"
    WAITING_REPLY = "waiting_reply"
    SNOOZED = "snoozed"


class TaskTypeEnum(str, Enum):
    DRAFT_REPLY = "draft_reply"
    SCHEDULE_MEETING = "schedule_meeting"
    REMIND_PENDING = "remind_pending"
    PAYMENT_INFO = "payment_info"
    FOLLOW_UP = "follow_up"
    INITIAL_OUTREACH = "initial_outreach"
    CUSTOM = "custom"


class TaskStatusEnum(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Influencer schemas
class InfluencerBase(BaseModel):
    name: str
    email: EmailStr
    status: InfluencerStatusEnum = InfluencerStatusEnum.PROSPECT
    risk_level: RiskLevelEnum = RiskLevelEnum.LOW
    instagram_handle: str | None = None
    twitter_handle: str | None = None
    tiktok_handle: str | None = None
    youtube_channel: str | None = None
    follower_count: int | None = None
    engagement_rate: float | None = None
    notes: str | None = None


class InfluencerCreate(InfluencerBase):
    hubspot_id: str | None = None


class InfluencerUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    status: InfluencerStatusEnum | None = None
    risk_level: RiskLevelEnum | None = None
    instagram_handle: str | None = None
    twitter_handle: str | None = None
    notes: str | None = None


class InfluencerResponse(InfluencerBase):
    id: UUID
    hubspot_id: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Conversation schemas
class ConversationBase(BaseModel):
    channel: ChannelEnum = ChannelEnum.EMAIL
    subject: str | None = None
    status: ConversationStatusEnum = ConversationStatusEnum.OPEN


class ConversationCreate(ConversationBase):
    influencer_id: UUID
    thread_id: str | None = None


class ConversationResponse(ConversationBase):
    id: UUID
    influencer_id: UUID
    thread_id: str | None
    last_message_at: datetime | None
    primary_intent: str | None
    sentiment: str | None
    priority: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Task schemas
class TaskBase(BaseModel):
    type: TaskTypeEnum
    draft_content: str | None = None
    draft_subject: str | None = None


class TaskCreate(TaskBase):
    conversation_id: UUID


class TaskResponse(TaskBase):
    id: UUID
    conversation_id: UUID
    status: TaskStatusEnum
    approval_user_id: str | None
    approval_timestamp: datetime | None
    rejection_reason: str | None
    confidence_score: float | None
    intent_detected: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskApproval(BaseModel):
    approved: bool
    user_id: str
    reason: str | None = None
    edited_content: str | None = None


# Email processing schemas
class EmailTriageRequest(BaseModel):
    message_id: str
    thread_id: str
    subject: str
    sender: str
    body: str
    timestamp: datetime


class EmailTriageResponse(BaseModel):
    intent: str
    confidence: float
    priority: str
    sentiment: str
    summary: str
    key_points: list[str]
    suggested_action: str
    requires_human: bool
    conversation_id: UUID | None = None
    task_id: UUID | None = None


class DraftRequest(BaseModel):
    conversation_id: UUID
    additional_instructions: str | None = None


class DraftResponse(BaseModel):
    task_id: UUID
    subject: str
    body: str
    confidence: float
    reasoning: str
    guardrail_passed: bool
    guardrail_warnings: list[str]


# Slack webhook schemas
class SlackEventPayload(BaseModel):
    type: str
    challenge: str | None = None
    event: dict[str, Any] | None = None
    token: str | None = None


class SlackInteractionPayload(BaseModel):
    type: str
    user: dict[str, str]
    actions: list[dict[str, Any]]
    response_url: str
    trigger_id: str


# Health check
class HealthResponse(BaseModel):
    status: str
    version: str
    database: bool
    redis: bool
    gmail: bool
    slack: bool


# Kill switch
class KillSwitchRequest(BaseModel):
    enabled: bool
    user_id: str


class KillSwitchResponse(BaseModel):
    enabled: bool
    changed_by: str
    changed_at: datetime


# Sync request
class SyncRequest(BaseModel):
    source: str = Field(..., description="Source to sync: hubspot, notion")
    full_sync: bool = False


class SyncResponse(BaseModel):
    source: str
    records_synced: int
    started_at: datetime
    completed_at: datetime | None
    status: str


# Dashboard schemas
class DashboardMetricsResponse(BaseModel):
    period_start: datetime
    period_end: datetime

    # Volume metrics
    total_emails_received: int
    total_emails_sent: int
    total_drafts_generated: int

    # Approval metrics
    approval_rate: float
    rejection_rate: float
    edit_rate: float

    # Performance metrics
    avg_response_time_seconds: float
    avg_confidence_score: float

    # Cost metrics
    total_tokens_used: int
    estimated_cost_usd: float

    # Error metrics
    error_count: int
    error_rate: float

    # Intent breakdown
    intent_distribution: dict[str, int]

    # Hourly breakdown
    hourly_volume: list[dict[str, Any]]

    # Time saved
    time_saved_minutes: float
    time_saved_hours: float


class RAGStatsResponse(BaseModel):
    sops: dict[str, Any]
    templates: dict[str, Any]
    conversations: dict[str, Any]
    similar_cases: dict[str, Any]


class WorkflowStatsResponse(BaseModel):
    active_workflows: int
    workflows_by_state: dict[str, int]
    timed_out_count: int


class AgentModeRequest(BaseModel):
    mode: str = Field(..., description="Agent mode: copilot or autopilot")


class AgentModeResponse(BaseModel):
    mode: str
    autopilot_confidence_threshold: float
    autopilot_allowed_intents: list[str]


# Prospecting schemas
class CampaignCreateRequest(BaseModel):
    name: str = Field(..., description="Campaign name")
    description: str = Field(..., description="Campaign description")
    target_status: str = Field(default="prospect", description="HubSpot lifecycle stage filter")
    target_tags: list[str] = Field(default_factory=list, description="Tags to filter by")
    max_prospects: int = Field(default=100, ge=1, le=1000, description="Maximum prospects to load")
    daily_limit: int = Field(default=20, ge=1, le=100, description="Daily sending limit")
    subject_template: str = Field(
        default="Collaboration opportunity with {brand_name}",
        description="Subject line template"
    )
    auto_approve: bool = Field(default=False, description="Auto-approve high-confidence drafts")
    confidence_threshold: float = Field(default=0.85, ge=0.5, le=1.0, description="Threshold for auto-approve")


class CampaignResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str
    target_status: str
    max_prospects: int
    daily_limit: int
    auto_approve: bool
    confidence_threshold: float
    prospects_loaded: int
    prospects_processed: int
    prospects_sent: int
    created_at: datetime


class CampaignProgressResponse(BaseModel):
    campaign_id: str
    total_prospects: int
    processed: int
    sent: int
    pending_approval: int
    failed: int
    skipped: int


class ProspectResultResponse(BaseModel):
    hubspot_id: str
    email: str
    status: str
    draft_subject: str | None
    confidence: float
    task_id: UUID | None
    error: str | None


class CampaignBatchResponse(BaseModel):
    campaign_id: str
    processed: int
    results: list[ProspectResultResponse]


# Autopilot schemas
class AutopilotDecisionResponse(BaseModel):
    task_id: UUID
    decision: str
    confidence: float
    reason: str
    can_auto_send: bool
    requires_senior_approval: bool
    blocked_reasons: list[str]
    warnings: list[str]
    timestamp: datetime


class AutopilotStatsResponse(BaseModel):
    total_decisions: int
    auto_sent: int
    human_review: int
    escalated: int
    blocked: int
    avg_confidence: float
    recent_decisions: list[dict[str, Any]]
