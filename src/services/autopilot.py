"""
Autopilot Decision Engine for automated email handling.

This module implements:
- Confidence scoring based on multiple factors
- Decision making for auto-send vs human review
- Safety checks before any automated action
- Audit logging for all autopilot decisions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from src.agents.triage import Intent, Priority, Sentiment
from src.core.config import settings
from src.core.logging import get_logger
from src.core.redis import check_kill_switch, get_redis
from src.services.guardrail import GuardrailResult, GuardrailService


class AutopilotDecision(StrEnum):
    """Possible autopilot decisions."""

    AUTO_SEND = "auto_send"
    HUMAN_REVIEW = "human_review"
    ESCALATE = "escalate"
    BLOCK = "block"


class ConfidenceSignal(StrEnum):
    """Signals that affect confidence scoring."""

    # Positive signals
    KNOWN_INFLUENCER = "known_influencer"
    ACTIVE_PARTNERSHIP = "active_partnership"
    SIMPLE_INTENT = "simple_intent"
    SOP_MATCH = "sop_match"
    TEMPLATE_USED = "template_used"
    LOW_RISK_INFLUENCER = "low_risk_influencer"
    CLEAR_QUESTION = "clear_question"

    # Negative signals
    NEW_INFLUENCER = "new_influencer"
    HIGH_RISK_INFLUENCER = "high_risk_influencer"
    FINANCIAL_TOPIC = "financial_topic"
    LEGAL_TOPIC = "legal_topic"
    FRUSTRATED_SENTIMENT = "frustrated_sentiment"
    COMPLEX_QUESTION = "complex_question"
    NO_SOP_MATCH = "no_sop_match"
    LONG_THREAD = "long_thread"
    BLACKLISTED = "blacklisted"


@dataclass
class ConfidenceScore:
    """Detailed confidence score breakdown."""

    overall: float
    intent_confidence: float
    content_confidence: float
    context_confidence: float
    safety_confidence: float

    signals: list[ConfidenceSignal] = field(default_factory=list)
    penalties: dict[str, float] = field(default_factory=dict)
    bonuses: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "intent_confidence": self.intent_confidence,
            "content_confidence": self.content_confidence,
            "context_confidence": self.context_confidence,
            "safety_confidence": self.safety_confidence,
            "signals": [s.value for s in self.signals],
            "penalties": self.penalties,
            "bonuses": self.bonuses,
        }


@dataclass
class AutopilotResult:
    """Result of autopilot decision."""

    decision: AutopilotDecision
    confidence: ConfidenceScore
    reason: str
    can_auto_send: bool
    requires_senior_approval: bool = False
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ConfidenceScorer:
    """
    Multi-factor confidence scoring system.

    Considers:
    - Triage confidence
    - Content quality (guardrails)
    - Influencer relationship
    - Intent complexity
    - SOP/template match
    """

    # Signal weights
    SIGNAL_WEIGHTS = {
        # Positive
        ConfidenceSignal.KNOWN_INFLUENCER: 0.1,
        ConfidenceSignal.ACTIVE_PARTNERSHIP: 0.15,
        ConfidenceSignal.SIMPLE_INTENT: 0.1,
        ConfidenceSignal.SOP_MATCH: 0.15,
        ConfidenceSignal.TEMPLATE_USED: 0.1,
        ConfidenceSignal.LOW_RISK_INFLUENCER: 0.1,
        ConfidenceSignal.CLEAR_QUESTION: 0.05,
        # Negative
        ConfidenceSignal.NEW_INFLUENCER: -0.1,
        ConfidenceSignal.HIGH_RISK_INFLUENCER: -0.3,
        ConfidenceSignal.FINANCIAL_TOPIC: -0.25,
        ConfidenceSignal.LEGAL_TOPIC: -0.3,
        ConfidenceSignal.FRUSTRATED_SENTIMENT: -0.2,
        ConfidenceSignal.COMPLEX_QUESTION: -0.15,
        ConfidenceSignal.NO_SOP_MATCH: -0.1,
        ConfidenceSignal.LONG_THREAD: -0.05,
        ConfidenceSignal.BLACKLISTED: -1.0,  # Automatic block
    }

    # Intent complexity scores (0 = simple, 1 = complex)
    INTENT_COMPLEXITY = {
        Intent.SCHEDULE_MEETING: 0.1,
        Intent.PAYMENT_INFO: 0.4,
        Intent.FOLLOW_UP: 0.2,
        Intent.GENERAL_QUESTION: 0.3,
        Intent.COLLAB_INQUIRY: 0.5,
        Intent.RATE_NEGOTIATION: 0.8,
        Intent.CONTRACT_QUESTION: 0.9,
        Intent.CONTENT_DELIVERY: 0.3,
        Intent.SPAM: 0.0,
        Intent.OUT_OF_SCOPE: 0.0,
    }

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)

    def calculate_score(
        self,
        triage_confidence: float,
        intent: Intent,
        sentiment: Sentiment,
        influencer_status: str | None,
        influencer_risk: str | None,
        guardrail_result: GuardrailResult,
        sop_sources_count: int,
        template_used: bool,
        thread_length: int,
        draft_confidence: float,
    ) -> ConfidenceScore:
        """Calculate comprehensive confidence score."""
        signals = []
        penalties = {}
        bonuses = {}

        # 1. Intent confidence (base from triage)
        intent_confidence = triage_confidence

        # Adjust for intent complexity
        complexity = self.INTENT_COMPLEXITY.get(intent, 0.5)
        if complexity < 0.3:
            signals.append(ConfidenceSignal.SIMPLE_INTENT)
            bonuses["simple_intent"] = 0.1
        elif complexity > 0.7:
            signals.append(ConfidenceSignal.COMPLEX_QUESTION)
            penalties["complex_intent"] = 0.15

        # 2. Content confidence (from guardrails)
        content_confidence = 1.0 - guardrail_result.risk_score
        if not guardrail_result.passed:
            content_confidence *= 0.5
            penalties["guardrail_violations"] = 0.2

        # 3. Context confidence (influencer relationship)
        context_confidence = 0.5  # Base

        # Influencer status signals
        if influencer_status == "active":
            signals.append(ConfidenceSignal.ACTIVE_PARTNERSHIP)
            signals.append(ConfidenceSignal.KNOWN_INFLUENCER)
            context_confidence = 0.8
            bonuses["active_partner"] = 0.15
        elif influencer_status == "prospect":
            signals.append(ConfidenceSignal.NEW_INFLUENCER)
            context_confidence = 0.4
            penalties["new_influencer"] = 0.1
        elif influencer_status == "blacklisted":
            signals.append(ConfidenceSignal.BLACKLISTED)
            context_confidence = 0.0
            penalties["blacklisted"] = 1.0

        # Risk level signals
        if influencer_risk == "high":
            signals.append(ConfidenceSignal.HIGH_RISK_INFLUENCER)
            penalties["high_risk"] = 0.3
        elif influencer_risk == "low":
            signals.append(ConfidenceSignal.LOW_RISK_INFLUENCER)
            bonuses["low_risk"] = 0.1

        # Sentiment signals
        if sentiment == Sentiment.FRUSTRATED:
            signals.append(ConfidenceSignal.FRUSTRATED_SENTIMENT)
            penalties["frustrated"] = 0.2
        elif sentiment == Sentiment.NEGATIVE:
            penalties["negative_sentiment"] = 0.1

        # 4. Safety confidence (SOP/template adherence)
        safety_confidence = draft_confidence

        if sop_sources_count > 0:
            signals.append(ConfidenceSignal.SOP_MATCH)
            bonuses["sop_match"] = 0.1 * min(sop_sources_count, 3)
        else:
            signals.append(ConfidenceSignal.NO_SOP_MATCH)
            penalties["no_sop"] = 0.1

        if template_used:
            signals.append(ConfidenceSignal.TEMPLATE_USED)
            bonuses["template"] = 0.1

        # Thread length penalty
        if thread_length > 10:
            signals.append(ConfidenceSignal.LONG_THREAD)
            penalties["long_thread"] = 0.05

        # Financial/legal topic detection
        if intent == Intent.RATE_NEGOTIATION:
            signals.append(ConfidenceSignal.FINANCIAL_TOPIC)
            penalties["financial"] = 0.25
        if intent == Intent.CONTRACT_QUESTION:
            signals.append(ConfidenceSignal.LEGAL_TOPIC)
            penalties["legal"] = 0.3

        # Calculate overall score
        base_score = (
            intent_confidence * 0.25
            + content_confidence * 0.25
            + context_confidence * 0.25
            + safety_confidence * 0.25
        )

        # Apply bonuses and penalties
        total_bonus = sum(bonuses.values())
        total_penalty = sum(penalties.values())
        overall = max(0.0, min(1.0, base_score + total_bonus - total_penalty))

        return ConfidenceScore(
            overall=overall,
            intent_confidence=intent_confidence,
            content_confidence=content_confidence,
            context_confidence=context_confidence,
            safety_confidence=safety_confidence,
            signals=signals,
            penalties=penalties,
            bonuses=bonuses,
        )


class AutopilotEngine:
    """
    Decision engine for autopilot mode.

    Determines whether an email can be auto-sent based on:
    - Confidence score
    - Intent allowlist
    - Influencer status
    - Guardrail results
    - Kill switch status
    """

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self.scorer = ConfidenceScorer()
        self.guardrail_service = GuardrailService()

    async def is_autopilot_enabled(self) -> bool:
        """Check if autopilot mode is currently enabled."""
        # Check kill switch first
        if await check_kill_switch():
            return False

        # Check Redis for runtime mode setting
        redis = await get_redis()
        mode = await redis.get("agent_mode")
        if mode:
            return mode == "autopilot"

        return settings.agent_mode == "autopilot"

    async def make_decision(
        self,
        intent: Intent,
        triage_confidence: float,
        sentiment: Sentiment,
        priority: Priority,
        influencer_status: str | None,
        influencer_risk: str | None,
        draft_content: str,
        draft_confidence: float,
        sop_sources: list[str],
        template_used: bool,
        thread_length: int,
    ) -> AutopilotResult:
        """Make autopilot decision for a draft."""
        blocked_reasons = []
        warnings = []

        # 1. Check kill switch
        if await check_kill_switch():
            return AutopilotResult(
                decision=AutopilotDecision.BLOCK,
                confidence=ConfidenceScore(0, 0, 0, 0, 0),
                reason="Kill switch is active",
                can_auto_send=False,
                blocked_reasons=["kill_switch_active"],
            )

        # 2. Check if autopilot is enabled
        if not await self.is_autopilot_enabled():
            return AutopilotResult(
                decision=AutopilotDecision.HUMAN_REVIEW,
                confidence=ConfidenceScore(0, 0, 0, 0, 0),
                reason="Copilot mode - all drafts require human review",
                can_auto_send=False,
            )

        # 3. Check blacklist
        if influencer_status == "blacklisted":
            return AutopilotResult(
                decision=AutopilotDecision.BLOCK,
                confidence=ConfidenceScore(0, 0, 0, 0, 0),
                reason="Influencer is blacklisted",
                can_auto_send=False,
                blocked_reasons=["influencer_blacklisted"],
            )

        # 4. Run guardrails
        guardrail_result = await self.guardrail_service.check_content(
            draft_content,
            check_pii=True,
            check_financial=True,
            check_policy=True,
        )

        if not guardrail_result.passed:
            blocked_reasons.extend([v.value for v in guardrail_result.violations])

        # 5. Calculate confidence score
        confidence = self.scorer.calculate_score(
            triage_confidence=triage_confidence,
            intent=intent,
            sentiment=sentiment,
            influencer_status=influencer_status,
            influencer_risk=influencer_risk,
            guardrail_result=guardrail_result,
            sop_sources_count=len(sop_sources),
            template_used=template_used,
            thread_length=thread_length,
            draft_confidence=draft_confidence,
        )

        # 6. Check intent allowlist
        intent_allowed = intent.value in settings.autopilot_allowed_intents
        if not intent_allowed:
            warnings.append(f"Intent '{intent.value}' not in autopilot allowlist")

        # 7. Check confidence threshold
        threshold = settings.autopilot_confidence_threshold
        meets_threshold = confidence.overall >= threshold

        # 8. Check for escalation conditions
        requires_escalation = (
            priority == Priority.URGENT
            or sentiment == Sentiment.FRUSTRATED
            or influencer_risk == "high"
            or ConfidenceSignal.FINANCIAL_TOPIC in confidence.signals
            or ConfidenceSignal.LEGAL_TOPIC in confidence.signals
        )

        # 9. Make decision
        if blocked_reasons:
            decision = AutopilotDecision.BLOCK
            reason = f"Blocked due to: {', '.join(blocked_reasons)}"
            can_auto_send = False
        elif requires_escalation:
            decision = AutopilotDecision.ESCALATE
            reason = "Requires senior approval due to risk factors"
            can_auto_send = False
        elif intent_allowed and meets_threshold and guardrail_result.passed:
            decision = AutopilotDecision.AUTO_SEND
            reason = (
                f"Confidence {confidence.overall:.2f} >= threshold"
                f" {threshold:.2f} for allowed intent"
            )
            can_auto_send = True
        else:
            decision = AutopilotDecision.HUMAN_REVIEW
            reasons = []
            if not intent_allowed:
                reasons.append("intent not allowed")
            if not meets_threshold:
                reasons.append(f"confidence {confidence.overall:.2f} < {threshold:.2f}")
            if not guardrail_result.passed:
                reasons.append("guardrail failed")
            reason = f"Human review required: {', '.join(reasons)}"
            can_auto_send = False

        self.logger.info(
            "Autopilot decision",
            decision=decision.value,
            confidence=confidence.overall,
            intent=intent.value,
            can_auto_send=can_auto_send,
        )

        return AutopilotResult(
            decision=decision,
            confidence=confidence,
            reason=reason,
            can_auto_send=can_auto_send,
            requires_senior_approval=requires_escalation,
            blocked_reasons=blocked_reasons,
            warnings=warnings + guardrail_result.suggestions,
        )

    async def record_decision(
        self,
        task_id: UUID,
        result: AutopilotResult,
        conversation_id: UUID,
    ) -> None:
        """Record autopilot decision for audit purposes."""
        redis = await get_redis()
        import json

        audit_key = f"autopilot:decision:{task_id}"
        audit_data = {
            "task_id": str(task_id),
            "conversation_id": str(conversation_id),
            "decision": result.decision.value,
            "confidence": result.confidence.to_dict(),
            "reason": result.reason,
            "can_auto_send": result.can_auto_send,
            "requires_senior_approval": result.requires_senior_approval,
            "blocked_reasons": result.blocked_reasons,
            "warnings": result.warnings,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Store for 30 days
        await redis.set(audit_key, json.dumps(audit_data), ex=86400 * 30)

        # Also store in list for recent decisions
        list_key = "autopilot:recent_decisions"
        await redis.lpush(list_key, json.dumps(audit_data))
        await redis.ltrim(list_key, 0, 999)  # Keep last 1000

    async def get_recent_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent autopilot decisions for monitoring."""
        redis = await get_redis()
        import json

        list_key = "autopilot:recent_decisions"
        data = await redis.lrange(list_key, 0, limit - 1)

        return [json.loads(d) for d in data]


# Singleton instance
_autopilot_engine: AutopilotEngine | None = None


def get_autopilot_engine() -> AutopilotEngine:
    """Get the autopilot engine singleton."""
    global _autopilot_engine
    if _autopilot_engine is None:
        _autopilot_engine = AutopilotEngine()
    return _autopilot_engine
