"""
Metrics Service for tracking agent performance and generating dashboard data.

Tracks:
- Response times (time to first draft, time to send)
- Approval rates (approved vs rejected)
- Intent distribution
- Confidence scores
- Token usage
- Error rates
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from src.core.logging import get_logger
from src.core.redis import get_redis


class MetricType(str, Enum):
    """Types of metrics tracked."""

    EMAILS_RECEIVED = "emails_received"
    EMAILS_SENT = "emails_sent"
    DRAFTS_GENERATED = "drafts_generated"
    DRAFTS_APPROVED = "drafts_approved"
    DRAFTS_REJECTED = "drafts_rejected"
    DRAFTS_EDITED = "drafts_edited"
    ERRORS = "errors"
    TOKENS_USED = "tokens_used"
    RESPONSE_TIME = "response_time"
    CONFIDENCE_SCORE = "confidence_score"


@dataclass
class DashboardMetrics:
    """Aggregated metrics for dashboard display."""

    period_start: datetime
    period_end: datetime

    # Volume metrics
    total_emails_received: int = 0
    total_emails_sent: int = 0
    total_drafts_generated: int = 0

    # Approval metrics
    approval_rate: float = 0.0
    rejection_rate: float = 0.0
    edit_rate: float = 0.0

    # Performance metrics
    avg_response_time_seconds: float = 0.0
    avg_confidence_score: float = 0.0

    # Cost metrics
    total_tokens_used: int = 0
    estimated_cost_usd: float = 0.0

    # Error metrics
    error_count: int = 0
    error_rate: float = 0.0

    # Intent breakdown
    intent_distribution: dict[str, int] = None

    # Hourly breakdown
    hourly_volume: list[dict[str, Any]] = None

    def __post_init__(self):
        if self.intent_distribution is None:
            self.intent_distribution = {}
        if self.hourly_volume is None:
            self.hourly_volume = []


@dataclass
class ConversationMetrics:
    """Metrics for a single conversation."""

    conversation_id: UUID
    started_at: datetime
    completed_at: datetime | None = None
    messages_count: int = 0
    drafts_count: int = 0
    approvals_count: int = 0
    rejections_count: int = 0
    total_response_time_seconds: float = 0.0
    tokens_used: int = 0
    final_outcome: str | None = None


class MetricsService:
    """Service for tracking and aggregating metrics."""

    # Redis key prefixes
    COUNTER_PREFIX = "metrics:counter"
    GAUGE_PREFIX = "metrics:gauge"
    HISTOGRAM_PREFIX = "metrics:histogram"
    TIMESERIES_PREFIX = "metrics:ts"

    # Cost per 1M tokens (approximate)
    COST_PER_MILLION_INPUT = 3.0  # Claude Sonnet input
    COST_PER_MILLION_OUTPUT = 15.0  # Claude Sonnet output

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)

    async def increment(
        self,
        metric: MetricType,
        value: int = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Increment a counter metric."""
        redis = await get_redis()
        key = self._build_key(self.COUNTER_PREFIX, metric, tags)

        await redis.incrby(key, value)

        # Also store in time series
        ts_key = self._build_timeseries_key(metric, tags)
        timestamp = int(datetime.utcnow().timestamp())
        await redis.hincrby(ts_key, str(timestamp // 3600 * 3600), value)

    async def record_value(
        self,
        metric: MetricType,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a gauge/histogram value."""
        redis = await get_redis()
        key = self._build_key(self.HISTOGRAM_PREFIX, metric, tags)

        # Store in sorted set for percentile calculations
        timestamp = datetime.utcnow().timestamp()
        await redis.zadd(key, {f"{timestamp}:{value}": timestamp})

        # Trim old values (keep last 24 hours)
        cutoff = timestamp - 86400
        await redis.zremrangebyscore(key, 0, cutoff)

    async def record_timing(
        self,
        metric: MetricType,
        duration_seconds: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a timing metric."""
        await self.record_value(metric, duration_seconds, tags)

    async def record_email_received(
        self,
        conversation_id: UUID,
        intent: str,
        confidence: float,
    ) -> None:
        """Record metrics for a received email."""
        await self.increment(MetricType.EMAILS_RECEIVED)
        await self.increment(
            MetricType.EMAILS_RECEIVED,
            tags={"intent": intent},
        )
        await self.record_value(
            MetricType.CONFIDENCE_SCORE,
            confidence,
            tags={"intent": intent},
        )

    async def record_draft_generated(
        self,
        conversation_id: UUID,
        tokens_used: int,
        generation_time_seconds: float,
    ) -> None:
        """Record metrics for a generated draft."""
        await self.increment(MetricType.DRAFTS_GENERATED)
        await self.increment(MetricType.TOKENS_USED, tokens_used)
        await self.record_timing(
            MetricType.RESPONSE_TIME,
            generation_time_seconds,
        )

    async def record_approval(
        self,
        conversation_id: UUID,
        approved: bool,
        was_edited: bool = False,
    ) -> None:
        """Record metrics for a draft approval/rejection."""
        if approved:
            await self.increment(MetricType.DRAFTS_APPROVED)
            if was_edited:
                await self.increment(MetricType.DRAFTS_EDITED)
        else:
            await self.increment(MetricType.DRAFTS_REJECTED)

    async def record_email_sent(
        self,
        conversation_id: UUID,
        total_time_seconds: float,
    ) -> None:
        """Record metrics for a sent email."""
        await self.increment(MetricType.EMAILS_SENT)
        await self.record_timing(
            MetricType.RESPONSE_TIME,
            total_time_seconds,
            tags={"stage": "total"},
        )

    async def record_error(
        self,
        error_type: str,
        conversation_id: UUID | None = None,
    ) -> None:
        """Record an error."""
        await self.increment(MetricType.ERRORS)
        await self.increment(
            MetricType.ERRORS,
            tags={"type": error_type},
        )

    async def get_counter(
        self,
        metric: MetricType,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Get current counter value."""
        redis = await get_redis()
        key = self._build_key(self.COUNTER_PREFIX, metric, tags)
        value = await redis.get(key)
        return int(value) if value else 0

    async def get_average(
        self,
        metric: MetricType,
        tags: dict[str, str] | None = None,
        hours: int = 24,
    ) -> float:
        """Get average value over time period."""
        redis = await get_redis()
        key = self._build_key(self.HISTOGRAM_PREFIX, metric, tags)

        cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        values = await redis.zrangebyscore(key, cutoff, "+inf")

        if not values:
            return 0.0

        total = sum(float(v.split(":")[1]) for v in values)
        return total / len(values)

    async def get_percentile(
        self,
        metric: MetricType,
        percentile: float = 0.95,
        tags: dict[str, str] | None = None,
        hours: int = 24,
    ) -> float:
        """Get percentile value over time period."""
        redis = await get_redis()
        key = self._build_key(self.HISTOGRAM_PREFIX, metric, tags)

        cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        values = await redis.zrangebyscore(key, cutoff, "+inf")

        if not values:
            return 0.0

        sorted_values = sorted(float(v.split(":")[1]) for v in values)
        index = int(len(sorted_values) * percentile)
        return sorted_values[min(index, len(sorted_values) - 1)]

    async def get_dashboard_metrics(
        self,
        hours: int = 24,
    ) -> DashboardMetrics:
        """Get aggregated metrics for dashboard display."""
        period_end = datetime.utcnow()
        period_start = period_end - timedelta(hours=hours)

        # Get counters
        emails_received = await self.get_counter(MetricType.EMAILS_RECEIVED)
        emails_sent = await self.get_counter(MetricType.EMAILS_SENT)
        drafts_generated = await self.get_counter(MetricType.DRAFTS_GENERATED)
        drafts_approved = await self.get_counter(MetricType.DRAFTS_APPROVED)
        drafts_rejected = await self.get_counter(MetricType.DRAFTS_REJECTED)
        drafts_edited = await self.get_counter(MetricType.DRAFTS_EDITED)
        errors = await self.get_counter(MetricType.ERRORS)
        tokens_used = await self.get_counter(MetricType.TOKENS_USED)

        # Calculate rates
        total_decisions = drafts_approved + drafts_rejected
        approval_rate = drafts_approved / total_decisions if total_decisions > 0 else 0.0
        rejection_rate = drafts_rejected / total_decisions if total_decisions > 0 else 0.0
        edit_rate = drafts_edited / drafts_approved if drafts_approved > 0 else 0.0
        error_rate = errors / drafts_generated if drafts_generated > 0 else 0.0

        # Get averages
        avg_response_time = await self.get_average(MetricType.RESPONSE_TIME, hours=hours)
        avg_confidence = await self.get_average(MetricType.CONFIDENCE_SCORE, hours=hours)

        # Calculate estimated cost
        estimated_cost = (tokens_used / 1_000_000) * (
            self.COST_PER_MILLION_INPUT * 0.3 + self.COST_PER_MILLION_OUTPUT * 0.7
        )

        # Get intent distribution
        intent_distribution = await self._get_intent_distribution()

        # Get hourly breakdown
        hourly_volume = await self._get_hourly_volume(hours)

        return DashboardMetrics(
            period_start=period_start,
            period_end=period_end,
            total_emails_received=emails_received,
            total_emails_sent=emails_sent,
            total_drafts_generated=drafts_generated,
            approval_rate=approval_rate,
            rejection_rate=rejection_rate,
            edit_rate=edit_rate,
            avg_response_time_seconds=avg_response_time,
            avg_confidence_score=avg_confidence,
            total_tokens_used=tokens_used,
            estimated_cost_usd=estimated_cost,
            error_count=errors,
            error_rate=error_rate,
            intent_distribution=intent_distribution,
            hourly_volume=hourly_volume,
        )

    async def _get_intent_distribution(self) -> dict[str, int]:
        """Get distribution of intents."""
        redis = await get_redis()
        distribution = {}

        intents = [
            "collab_inquiry",
            "rate_negotiation",
            "schedule_meeting",
            "payment_info",
            "contract_question",
            "content_delivery",
            "general_question",
            "follow_up",
            "spam",
        ]

        for intent in intents:
            key = self._build_key(
                self.COUNTER_PREFIX,
                MetricType.EMAILS_RECEIVED,
                {"intent": intent},
            )
            value = await redis.get(key)
            if value:
                distribution[intent] = int(value)

        return distribution

    async def _get_hourly_volume(self, hours: int) -> list[dict[str, Any]]:
        """Get hourly volume breakdown."""
        redis = await get_redis()
        hourly = []

        ts_key = self._build_timeseries_key(MetricType.EMAILS_RECEIVED)
        data = await redis.hgetall(ts_key)

        now = datetime.utcnow()
        for i in range(hours):
            hour_start = now - timedelta(hours=i + 1)
            hour_key = str(int(hour_start.timestamp()) // 3600 * 3600)
            count = int(data.get(hour_key, 0))
            hourly.append({
                "hour": hour_start.isoformat(),
                "count": count,
            })

        return list(reversed(hourly))

    def _build_key(
        self,
        prefix: str,
        metric: MetricType,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Build a Redis key for a metric."""
        key = f"{prefix}:{metric.value}"
        if tags:
            tag_str = ":".join(f"{k}={v}" for k, v in sorted(tags.items()))
            key = f"{key}:{tag_str}"
        return key

    def _build_timeseries_key(
        self,
        metric: MetricType,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Build a Redis key for time series data."""
        return self._build_key(self.TIMESERIES_PREFIX, metric, tags)

    async def reset_metrics(self) -> None:
        """Reset all metrics (for testing)."""
        redis = await get_redis()
        keys = []
        cursor = 0

        while True:
            cursor, batch = await redis.scan(cursor, match="metrics:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        if keys:
            await redis.delete(*keys)

        self.logger.info("Metrics reset", keys_deleted=len(keys))


# Time savings calculation
class TimeSavingsCalculator:
    """Calculate time saved by the agent."""

    # Average time in minutes for manual tasks
    MANUAL_TIMES = {
        "triage": 2,
        "draft": 10,
        "review": 3,
        "send": 1,
    }

    @classmethod
    def calculate_time_saved(
        cls,
        emails_triaged: int,
        drafts_generated: int,
        emails_sent: int,
    ) -> dict[str, Any]:
        """Calculate total time saved."""
        triage_time = emails_triaged * cls.MANUAL_TIMES["triage"]
        draft_time = drafts_generated * cls.MANUAL_TIMES["draft"]
        send_time = emails_sent * cls.MANUAL_TIMES["send"]

        total_minutes = triage_time + draft_time + send_time
        hours = total_minutes / 60

        return {
            "total_minutes": total_minutes,
            "total_hours": hours,
            "breakdown": {
                "triage": triage_time,
                "drafting": draft_time,
                "sending": send_time,
            },
            "equivalent_fte_hours": hours,
        }
