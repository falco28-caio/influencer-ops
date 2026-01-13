from __future__ import annotations

"""
Audit Logging Service for comprehensive action tracking.

This module implements:
- Structured audit logging for all agent actions
- LLM interaction tracking
- Compliance reporting
- Searchable audit trail
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID

from src.core.database import get_session_context
from src.core.logging import get_logger
from src.core.redis import get_redis
from src.models import AuditLog


class AuditCategory(str, Enum):
    """Categories of audit events."""
    EMAIL = "email"
    DRAFT = "draft"
    APPROVAL = "approval"
    AUTOPILOT = "autopilot"
    PROSPECTING = "prospecting"
    GUARDRAIL = "guardrail"
    SYSTEM = "system"
    USER_ACTION = "user_action"
    LLM_CALL = "llm_call"
    INTEGRATION = "integration"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditEvent:
    """Structured audit event."""
    category: AuditCategory
    action: str
    actor: str
    actor_type: str  # "user", "agent", "system", "autopilot"
    severity: AuditSeverity = AuditSeverity.INFO
    task_id: UUID | None = None
    conversation_id: UUID | None = None
    influencer_id: UUID | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class LLMCallLog:
    """Log of an LLM API call."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    purpose: str  # "triage", "drafting", "guardrail", etc.
    task_id: UUID | None = None
    success: bool = True
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


class AuditService:
    """
    Comprehensive audit logging service.

    Tracks all significant actions for:
    - Compliance reporting
    - Debugging and troubleshooting
    - Performance analysis
    - Security monitoring
    """

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)

    async def log_event(self, event: AuditEvent) -> None:
        """Log an audit event to both database and Redis."""
        # Log to structured logger
        log_data = {
            "category": event.category.value,
            "action": event.action,
            "actor": event.actor,
            "actor_type": event.actor_type,
            "severity": event.severity.value,
            "task_id": str(event.task_id) if event.task_id else None,
            "conversation_id": str(event.conversation_id) if event.conversation_id else None,
            "influencer_id": str(event.influencer_id) if event.influencer_id else None,
            "description": event.description,
            "metadata": event.metadata,
        }

        if event.severity == AuditSeverity.ERROR:
            self.logger.error("Audit event", **log_data)
        elif event.severity == AuditSeverity.WARNING:
            self.logger.warning("Audit event", **log_data)
        elif event.severity == AuditSeverity.CRITICAL:
            self.logger.critical("Audit event", **log_data)
        else:
            self.logger.info("Audit event", **log_data)

        # Store in database if task_id is present
        if event.task_id:
            async with get_session_context() as session:
                audit_log = AuditLog(
                    task_id=event.task_id,
                    action=event.action,
                    actor=event.actor,
                    actor_type=event.actor_type,
                    timestamp=event.timestamp,
                    description=event.description,
                    metadata={
                        "category": event.category.value,
                        "severity": event.severity.value,
                        **event.metadata,
                    },
                )
                session.add(audit_log)

        # Store in Redis for real-time queries
        await self._store_in_redis(event)

    async def _store_in_redis(self, event: AuditEvent) -> None:
        """Store event in Redis for fast querying."""
        redis = await get_redis()
        import json

        event_data = {
            "category": event.category.value,
            "action": event.action,
            "actor": event.actor,
            "actor_type": event.actor_type,
            "severity": event.severity.value,
            "task_id": str(event.task_id) if event.task_id else None,
            "conversation_id": str(event.conversation_id) if event.conversation_id else None,
            "influencer_id": str(event.influencer_id) if event.influencer_id else None,
            "description": event.description,
            "metadata": event.metadata,
            "timestamp": event.timestamp.isoformat(),
        }

        # Store in recent events list
        await redis.lpush("audit:recent", json.dumps(event_data))
        await redis.ltrim("audit:recent", 0, 9999)  # Keep last 10,000 events
        await redis.expire("audit:recent", 86400 * 7)  # 7 days

        # Store by category for filtering
        category_key = f"audit:category:{event.category.value}"
        await redis.lpush(category_key, json.dumps(event_data))
        await redis.ltrim(category_key, 0, 999)
        await redis.expire(category_key, 86400 * 7)

        # Store critical events separately
        if event.severity in [AuditSeverity.ERROR, AuditSeverity.CRITICAL]:
            await redis.lpush("audit:critical", json.dumps(event_data))
            await redis.ltrim("audit:critical", 0, 499)

    async def log_llm_call(self, llm_log: LLMCallLog) -> None:
        """Log an LLM API call for cost and performance tracking."""
        redis = await get_redis()
        import json

        log_data = {
            "model": llm_log.model,
            "prompt_tokens": llm_log.prompt_tokens,
            "completion_tokens": llm_log.completion_tokens,
            "total_tokens": llm_log.total_tokens,
            "latency_ms": llm_log.latency_ms,
            "purpose": llm_log.purpose,
            "task_id": str(llm_log.task_id) if llm_log.task_id else None,
            "success": llm_log.success,
            "error": llm_log.error,
            "timestamp": llm_log.timestamp.isoformat(),
        }

        # Store in LLM calls list
        await redis.lpush("audit:llm_calls", json.dumps(log_data))
        await redis.ltrim("audit:llm_calls", 0, 9999)
        await redis.expire("audit:llm_calls", 86400 * 30)  # 30 days

        # Update daily token counts
        date_key = llm_log.timestamp.strftime("%Y-%m-%d")
        await redis.hincrby(f"audit:tokens:{date_key}", "prompt", llm_log.prompt_tokens)
        await redis.hincrby(f"audit:tokens:{date_key}", "completion", llm_log.completion_tokens)
        await redis.hincrby(f"audit:tokens:{date_key}", "total", llm_log.total_tokens)
        await redis.expire(f"audit:tokens:{date_key}", 86400 * 90)  # 90 days

        # Track by model
        await redis.hincrby(f"audit:tokens:model:{llm_log.model}", "total", llm_log.total_tokens)
        await redis.hincrby(f"audit:tokens:model:{llm_log.model}", "calls", 1)

        self.logger.debug(
            "LLM call logged",
            model=llm_log.model,
            tokens=llm_log.total_tokens,
            latency_ms=llm_log.latency_ms,
            purpose=llm_log.purpose,
        )

    async def get_recent_events(
        self,
        limit: int = 100,
        category: AuditCategory | None = None,
        severity: AuditSeverity | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent audit events."""
        redis = await get_redis()
        import json

        if category:
            key = f"audit:category:{category.value}"
        elif severity in [AuditSeverity.ERROR, AuditSeverity.CRITICAL]:
            key = "audit:critical"
        else:
            key = "audit:recent"

        data = await redis.lrange(key, 0, limit - 1)
        events = [json.loads(d) for d in data]

        # Filter by severity if specified and not already filtered
        if severity and key == "audit:recent":
            events = [e for e in events if e.get("severity") == severity.value]

        return events

    async def get_llm_usage_stats(self, days: int = 7) -> dict[str, Any]:
        """Get LLM usage statistics."""
        redis = await get_redis()

        stats = {
            "daily_tokens": [],
            "by_model": {},
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }

        # Get daily stats
        for i in range(days):
            date = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
            data = await redis.hgetall(f"audit:tokens:{date}")
            if data:
                stats["daily_tokens"].append({
                    "date": date,
                    "prompt": int(data.get("prompt", 0)),
                    "completion": int(data.get("completion", 0)),
                    "total": int(data.get("total", 0)),
                })
                stats["total_tokens"] += int(data.get("total", 0))

        # Estimate cost (using approximate rates)
        # Claude Haiku: ~$0.25/1M input, ~$1.25/1M output
        # Claude Sonnet: ~$3/1M input, ~$15/1M output
        stats["total_cost_usd"] = stats["total_tokens"] * 0.000008  # Rough average

        return stats

    async def get_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Generate compliance report for date range."""
        redis = await get_redis()
        import json

        # Get all events in range
        all_events = await redis.lrange("audit:recent", 0, -1)
        events = []
        for e in all_events:
            event = json.loads(e)
            event_time = datetime.fromisoformat(event["timestamp"])
            if start_date <= event_time <= end_date:
                events.append(event)

        # Calculate statistics
        total_events = len(events)
        by_category = {}
        by_severity = {}
        by_actor_type = {}
        autopilot_decisions = {"auto_send": 0, "human_review": 0, "escalated": 0, "blocked": 0}

        for event in events:
            cat = event.get("category", "unknown")
            sev = event.get("severity", "info")
            actor = event.get("actor_type", "unknown")

            by_category[cat] = by_category.get(cat, 0) + 1
            by_severity[sev] = by_severity.get(sev, 0) + 1
            by_actor_type[actor] = by_actor_type.get(actor, 0) + 1

            # Track autopilot decisions
            if event.get("action", "").startswith("autopilot_"):
                decision = event.get("metadata", {}).get("decision", "unknown")
                if decision in autopilot_decisions:
                    autopilot_decisions[decision] += 1

        return {
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "total_events": total_events,
            "by_category": by_category,
            "by_severity": by_severity,
            "by_actor_type": by_actor_type,
            "autopilot_decisions": autopilot_decisions,
            "critical_events": by_severity.get("critical", 0) + by_severity.get("error", 0),
        }

    # Convenience methods for common audit events
    async def log_email_received(
        self,
        task_id: UUID | None,
        conversation_id: UUID,
        influencer_id: UUID,
        sender: str,
        subject: str,
    ) -> None:
        """Log email received event."""
        await self.log_event(AuditEvent(
            category=AuditCategory.EMAIL,
            action="email_received",
            actor="system",
            actor_type="system",
            task_id=task_id,
            conversation_id=conversation_id,
            influencer_id=influencer_id,
            description=f"Email received from {sender}",
            metadata={"sender": sender, "subject": subject},
        ))

    async def log_email_sent(
        self,
        task_id: UUID,
        conversation_id: UUID,
        influencer_id: UUID,
        recipient: str,
        subject: str,
        sent_by: str,
    ) -> None:
        """Log email sent event."""
        await self.log_event(AuditEvent(
            category=AuditCategory.EMAIL,
            action="email_sent",
            actor=sent_by,
            actor_type="agent" if sent_by in ["autopilot", "system"] else "user",
            task_id=task_id,
            conversation_id=conversation_id,
            influencer_id=influencer_id,
            description=f"Email sent to {recipient}",
            metadata={"recipient": recipient, "subject": subject},
        ))

    async def log_draft_generated(
        self,
        task_id: UUID,
        conversation_id: UUID,
        intent: str,
        confidence: float,
    ) -> None:
        """Log draft generation event."""
        await self.log_event(AuditEvent(
            category=AuditCategory.DRAFT,
            action="draft_generated",
            actor="drafting_agent",
            actor_type="agent",
            task_id=task_id,
            conversation_id=conversation_id,
            description=f"Draft generated for intent: {intent}",
            metadata={"intent": intent, "confidence": confidence},
        ))

    async def log_approval_decision(
        self,
        task_id: UUID,
        user_id: str,
        approved: bool,
        edited: bool = False,
    ) -> None:
        """Log approval decision event."""
        action = "draft_approved" if approved else "draft_rejected"
        await self.log_event(AuditEvent(
            category=AuditCategory.APPROVAL,
            action=action,
            actor=user_id,
            actor_type="user",
            task_id=task_id,
            description=f"Draft {'approved' + (' with edits' if edited else '') if approved else 'rejected'}",
            metadata={"approved": approved, "edited": edited},
        ))

    async def log_autopilot_decision(
        self,
        task_id: UUID,
        conversation_id: UUID,
        decision: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Log autopilot decision event."""
        severity = AuditSeverity.WARNING if decision in ["escalate", "block"] else AuditSeverity.INFO
        await self.log_event(AuditEvent(
            category=AuditCategory.AUTOPILOT,
            action=f"autopilot_{decision}",
            actor="autopilot",
            actor_type="autopilot",
            severity=severity,
            task_id=task_id,
            conversation_id=conversation_id,
            description=f"Autopilot decision: {decision} - {reason}",
            metadata={"decision": decision, "confidence": confidence, "reason": reason},
        ))

    async def log_guardrail_violation(
        self,
        task_id: UUID,
        violations: list[str],
        risk_score: float,
    ) -> None:
        """Log guardrail violation event."""
        severity = AuditSeverity.CRITICAL if risk_score > 0.8 else AuditSeverity.WARNING
        await self.log_event(AuditEvent(
            category=AuditCategory.GUARDRAIL,
            action="guardrail_violation",
            actor="guardrail_service",
            actor_type="agent",
            severity=severity,
            task_id=task_id,
            description=f"Guardrail violations detected: {', '.join(violations)}",
            metadata={"violations": violations, "risk_score": risk_score},
        ))

    async def log_security_event(
        self,
        action: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log security-related event."""
        await self.log_event(AuditEvent(
            category=AuditCategory.SYSTEM,
            action=action,
            actor="security_monitor",
            actor_type="system",
            severity=AuditSeverity.WARNING,
            description=description,
            metadata=metadata or {},
        ))


# Singleton instance
_audit_service: AuditService | None = None


def get_audit_service() -> AuditService:
    """Get the audit service singleton."""
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service
