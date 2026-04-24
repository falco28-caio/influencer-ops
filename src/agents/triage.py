from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import anthropic

from src.core.config import settings
from src.core.logging import get_logger


class Intent(StrEnum):
    """Email intent categories."""

    COLLAB_INQUIRY = "collab_inquiry"
    RATE_NEGOTIATION = "rate_negotiation"
    SCHEDULE_MEETING = "schedule_meeting"
    PAYMENT_INFO = "payment_info"
    CONTRACT_QUESTION = "contract_question"
    CONTENT_DELIVERY = "content_delivery"
    GENERAL_QUESTION = "general_question"
    FOLLOW_UP = "follow_up"
    SPAM = "spam"
    OUT_OF_SCOPE = "out_of_scope"


class Priority(StrEnum):
    """Email priority levels."""

    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Sentiment(StrEnum):
    """Email sentiment categories."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    FRUSTRATED = "frustrated"


@dataclass
class TriageResult:
    """Result of email triage analysis."""

    intent: Intent
    confidence: float
    priority: Priority
    sentiment: Sentiment
    summary: str
    key_points: list[str]
    suggested_action: str
    requires_human: bool
    context_needed: list[str]
    raw_analysis: dict[str, Any]


class TriageAgent:
    """Agent for classifying and triaging incoming emails."""

    SYSTEM_PROMPT = """You are an expert email triage agent for an influencer operations team.
Your job is to analyze incoming emails from influencers and classify them appropriately.

You must output a JSON object with the following structure:
{
    "intent": "<intent>",
    "confidence": <0.0-1.0>,
    "priority": "<priority>",
    "sentiment": "<sentiment>",
    "summary": "<1-2 sentence summary>",
    "key_points": ["<point1>", "<point2>"],
    "suggested_action": "<what should be done>",
    "requires_human": <true/false>,
    "context_needed": ["<context1>", "<context2>"]
}

Intent options:
- collab_inquiry: New collaboration or partnership request
- rate_negotiation: Discussion about rates, pricing, or compensation
- schedule_meeting: Request to schedule a call or meeting
- payment_info: Questions about payment, invoices, or banking
- contract_question: Questions about contract terms or agreements
- content_delivery: Updates about content creation or delivery
- general_question: General inquiries that don't fit other categories
- follow_up: Following up on a previous conversation
- spam: Irrelevant or promotional content
- out_of_scope: Not related to influencer operations

Priority options:
- urgent: Requires immediate attention (deadline, angry influencer, payment issue)
- high: Important but not time-critical
- normal: Standard business communication
- low: Can be addressed when convenient

Sentiment options:
- positive: Happy, excited, collaborative tone
- neutral: Business-like, professional
- negative: Disappointed, concerned
- frustrated: Angry, upset, threatening

Set requires_human to true if:
- The email involves financial commitments
- The influencer seems upset or frustrated
- Legal or contract issues are mentioned
- You're not confident in your classification (confidence < 0.7)
- The situation is complex or unusual"""

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )

    async def analyze(
        self,
        email_body: str,
        email_subject: str,
        sender: str,
        thread_history: list[dict[str, str]] | None = None,
        influencer_context: dict[str, Any] | None = None,
    ) -> TriageResult:
        """Analyze an email and return triage classification."""
        context_parts = []

        if influencer_context:
            context_parts.append(f"Influencer Info: {json.dumps(influencer_context)}")

        if thread_history:
            history_text = "\n".join(
                f"[{msg['direction']}] {msg['content'][:500]}"
                for msg in thread_history[-5:]  # Last 5 messages
            )
            context_parts.append(f"Thread History:\n{history_text}")

        context = "\n\n".join(context_parts) if context_parts else "No prior context available."

        user_prompt = f"""Analyze this email:

From: {sender}
Subject: {email_subject}

Body:
{email_body}

Context:
{context}

Provide your analysis as a JSON object."""

        try:
            response = self._client.messages.create(
                model=settings.llm_model,
                max_tokens=1024,
                temperature=0.3,
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            analysis = self._parse_response(response_text)

            return TriageResult(
                intent=Intent(analysis.get("intent", "general_question")),
                confidence=float(analysis.get("confidence", 0.5)),
                priority=Priority(analysis.get("priority", "normal")),
                sentiment=Sentiment(analysis.get("sentiment", "neutral")),
                summary=analysis.get("summary", ""),
                key_points=analysis.get("key_points", []),
                suggested_action=analysis.get("suggested_action", ""),
                requires_human=analysis.get("requires_human", True),
                context_needed=analysis.get("context_needed", []),
                raw_analysis=analysis,
            )
        except Exception as e:
            self.logger.error("Triage analysis failed", error=str(e))
            # Return safe default on error
            return TriageResult(
                intent=Intent.GENERAL_QUESTION,
                confidence=0.0,
                priority=Priority.NORMAL,
                sentiment=Sentiment.NEUTRAL,
                summary="Analysis failed - requires manual review",
                key_points=[],
                suggested_action="Manual review required",
                requires_human=True,
                context_needed=[],
                raw_analysis={"error": str(e)},
            )

    def _parse_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM response."""
        # Try to find JSON in the response
        response = response.strip()

        # Handle markdown code blocks
        if "```json" in response:
            start = response.find("```json") + 7
            end = response.find("```", start)
            response = response[start:end].strip()
        elif "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            response = response[start:end].strip()

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            self.logger.warning("Failed to parse JSON response", response=response[:200])
            return {}

    async def quick_classify(self, email_body: str) -> tuple[Intent, float]:
        """Quick classification without full analysis."""
        # Simple keyword-based classification for speed
        body_lower = email_body.lower()

        if any(word in body_lower for word in ["spam", "unsubscribe", "promotion"]):
            return Intent.SPAM, 0.8

        if any(word in body_lower for word in ["rate", "price", "fee", "budget", "cost"]):
            return Intent.RATE_NEGOTIATION, 0.7

        if any(word in body_lower for word in ["schedule", "meeting", "call", "calendar"]):
            return Intent.SCHEDULE_MEETING, 0.8

        if any(word in body_lower for word in ["payment", "invoice", "bank", "wire"]):
            return Intent.PAYMENT_INFO, 0.8

        if any(word in body_lower for word in ["contract", "agreement", "terms", "sign"]):
            return Intent.CONTRACT_QUESTION, 0.7

        if any(word in body_lower for word in ["collab", "partner", "work together", "campaign"]):
            return Intent.COLLAB_INQUIRY, 0.7

        if any(word in body_lower for word in ["follow up", "checking in", "any update"]):
            return Intent.FOLLOW_UP, 0.7

        return Intent.GENERAL_QUESTION, 0.5
