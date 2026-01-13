from __future__ import annotations

"""
Enhanced Drafting Service with template support and RAG integration.

Features:
- Template-based drafting for consistent messaging
- Full RAG context integration
- Confidence scoring
- Style adaptation based on influencer relationship
- Quote mode for SOP compliance
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic

from src.agents.triage import Intent
from src.core.config import settings
from src.core.logging import get_logger
from src.services.rag import RAGContext


@dataclass
class DraftResult:
    """Result of email draft generation."""

    subject: str
    body: str
    confidence: float
    reasoning: str
    sources_used: list[str]
    tokens_used: int
    template_used: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class DraftTemplate:
    """Email template for consistent messaging."""

    name: str
    intent: Intent
    subject_template: str
    body_template: str
    tone: str  # formal, friendly, apologetic, enthusiastic
    required_placeholders: list[str]
    optional_placeholders: list[str]


class DraftingService:
    """Enhanced service for generating email draft responses."""

    SYSTEM_PROMPT = """You are an expert email writer for an influencer operations team.
Your job is to draft professional, friendly, and effective email responses to influencers.

CRITICAL GUIDELINES:
1. Be warm but professional - influencers are partners, not just contacts
2. Be concise - get to the point while maintaining friendliness
3. Use the provided SOPs and context to ensure accurate information
4. NEVER make promises about money, rates, or commitments without explicit approval
5. NEVER invent information - only use what's provided in the context
6. If unsure, acknowledge the question and offer to find out
7. Always maintain a positive, collaborative tone

QUOTE MODE (when SOP content is provided):
- You MUST quote or closely paraphrase the SOP content
- Do not invent policies or procedures
- If the SOP doesn't cover something, say "I'll need to check on that"

STYLE ADAPTATION:
- New influencers: More formal, welcoming, establish rapport
- Active partners: Friendly, efficient, acknowledge history
- High-risk: Extra careful, document everything, avoid commitments

Output a JSON object with:
{
    "subject": "<email subject line>",
    "body": "<full email body>",
    "confidence": <0.0-1.0 how confident you are>,
    "reasoning": "<brief explanation of your approach>",
    "sources_used": ["<list of SOPs or context used>"],
    "warnings": ["<any concerns about the draft>"]
}"""

    # Pre-defined templates for common scenarios
    TEMPLATES: dict[Intent, DraftTemplate] = {
        Intent.COLLAB_INQUIRY: DraftTemplate(
            name="collaboration_response",
            intent=Intent.COLLAB_INQUIRY,
            subject_template="Re: Partnership Opportunity - {brand_name}",
            body_template="""Hi {influencer_name},

Thank you so much for reaching out about a potential partnership! We're excited about the possibility of working together.

{personalized_comment}

To help us understand how we might collaborate, could you share:
- The type of content you'd envision creating
- Your typical timeline for sponsored content
- Any specific campaign ideas you have in mind

{next_steps}

Looking forward to hearing from you!

Best,
{sender_name}
{team_name}""",
            tone="enthusiastic",
            required_placeholders=["influencer_name", "sender_name", "team_name"],
            optional_placeholders=["personalized_comment", "next_steps", "brand_name"],
        ),
        Intent.SCHEDULE_MEETING: DraftTemplate(
            name="meeting_scheduling",
            intent=Intent.SCHEDULE_MEETING,
            subject_template="Re: Let's Chat - Scheduling Our Call",
            body_template="""Hi {influencer_name},

Thanks for wanting to connect! I'd love to find a time that works for both of us.

{availability_text}

You can also book directly using this link: {calendar_link}

What works best for you?

Best,
{sender_name}""",
            tone="friendly",
            required_placeholders=["influencer_name", "sender_name"],
            optional_placeholders=["availability_text", "calendar_link"],
        ),
        Intent.PAYMENT_INFO: DraftTemplate(
            name="payment_inquiry",
            intent=Intent.PAYMENT_INFO,
            subject_template="Re: Payment Information",
            body_template="""Hi {influencer_name},

Thanks for reaching out about payment details.

{payment_info}

{timeline_info}

If you have any questions about the payment process, please don't hesitate to ask.

Best,
{sender_name}""",
            tone="formal",
            required_placeholders=["influencer_name", "sender_name", "payment_info"],
            optional_placeholders=["timeline_info"],
        ),
        Intent.FOLLOW_UP: DraftTemplate(
            name="follow_up_response",
            intent=Intent.FOLLOW_UP,
            subject_template="Re: Following Up",
            body_template="""Hi {influencer_name},

Thanks for checking in! {apology_if_needed}

{status_update}

{next_steps}

Please let me know if you have any questions.

Best,
{sender_name}""",
            tone="apologetic",
            required_placeholders=["influencer_name", "sender_name", "status_update"],
            optional_placeholders=["apology_if_needed", "next_steps"],
        ),
    }

    INTENT_PROMPTS: dict[Intent, str] = {
        Intent.COLLAB_INQUIRY: """This is a new collaboration inquiry.
- Express enthusiasm about potential partnership
- Ask clarifying questions about their vision
- Mention next steps for moving forward
- Reference any relevant campaigns or brand guidelines from SOPs""",
        Intent.RATE_NEGOTIATION: """This involves rate/pricing discussion.
- Be diplomatic and professional
- Refer to standard rate cards if available in context
- Avoid committing to specific numbers without approval
- Suggest a call to discuss details
- FLAG: Any mention of specific dollar amounts needs human review""",
        Intent.SCHEDULE_MEETING: """This is a meeting scheduling request.
- Be accommodating with timing
- Offer specific time slots if possible
- Confirm the meeting purpose
- Include calendar booking link if available""",
        Intent.PAYMENT_INFO: """This is about payment information.
- Be precise about payment processes
- Reference official payment SOPs EXACTLY
- Never share banking details in email
- Direct to appropriate payment portal
- FLAG: Never promise specific payment dates without checking""",
        Intent.CONTRACT_QUESTION: """This is about contract terms.
- Be helpful but cautious
- Do not interpret legal terms
- Refer to specific contract clauses if known
- ALWAYS suggest involving legal team for complex questions
- FLAG: Do not paraphrase contract terms""",
        Intent.CONTENT_DELIVERY: """This is about content delivery.
- Confirm receipt if content was sent
- Provide clear feedback if needed
- Discuss next steps in the workflow
- Be encouraging about their work""",
        Intent.FOLLOW_UP: """This is a follow-up message.
- Apologize for any delay if appropriate
- Provide requested update or information
- Set clear expectations for next steps""",
        Intent.GENERAL_QUESTION: """This is a general inquiry.
- Address the specific question asked
- Be helpful and thorough
- Offer additional assistance if needed""",
    }

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )

    async def generate_draft(
        self,
        intent: Intent,
        original_email: str,
        original_subject: str,
        sender_name: str,
        sop_content: str | None = None,
        thread_history: list[dict[str, str]] | None = None,
        influencer_context: dict[str, Any] | None = None,
        additional_instructions: str | None = None,
        use_template: bool = True,
        rag_context: RAGContext | None = None,
    ) -> DraftResult:
        """Generate an email draft response with full context."""
        start_time = datetime.utcnow()
        context_parts = []
        sources_used = []

        # Determine style based on influencer relationship
        style_guidance = self._get_style_guidance(influencer_context)

        # Add intent-specific guidance
        intent_guidance = self.INTENT_PROMPTS.get(intent, "")
        if intent_guidance:
            context_parts.append(f"## Intent Guidance\n{intent_guidance}")

        # Add style guidance
        context_parts.append(f"## Style\n{style_guidance}")

        # Add template if available and requested
        template = self.TEMPLATES.get(intent) if use_template else None
        template_used = None
        if template:
            template_used = template.name
            context_parts.append(f"## Template Reference\n{template.body_template}")
            context_parts.append(f"Tone: {template.tone}")

        # Use RAG context if provided
        if rag_context:
            if rag_context.sop_content:
                context_parts.append(f"## SOP Content (MUST FOLLOW)\n{rag_context.sop_content}")
                sources_used.extend(rag_context.sources)
            if rag_context.conversation_history:
                context_parts.append(f"## Previous Messages\n{rag_context.conversation_history}")
            if rag_context.influencer_context:
                context_parts.append(f"## Influencer Info\n{rag_context.influencer_context}")
            if rag_context.similar_cases:
                context_parts.append(f"## Similar Past Cases\n" + "\n---\n".join(rag_context.similar_cases[:2]))
        elif sop_content:
            context_parts.append(f"## SOP Content (MUST FOLLOW)\n{sop_content}")
            sources_used.append("SOP Documents")

        # Add thread history
        if thread_history:
            history_text = "\n---\n".join(
                f"[{msg['direction'].upper()}]\n{msg['content']}"
                for msg in thread_history[-5:]
            )
            context_parts.append(f"## Conversation History\n{history_text}")

        # Add influencer context
        if influencer_context:
            inf_text = json.dumps(influencer_context, indent=2)
            context_parts.append(f"## Influencer Details\n{inf_text}")

        # Add additional instructions
        if additional_instructions:
            context_parts.append(f"## Special Instructions\n{additional_instructions}")

        context = "\n\n---\n\n".join(context_parts) if context_parts else "No additional context."

        user_prompt = f"""Draft a reply to this email:

From: {sender_name}
Subject: {original_subject}

Email Content:
{original_email}

---

Context, SOPs & Guidelines:
{context}

Generate your response as a JSON object with subject, body, confidence, reasoning, sources_used, and warnings."""

        try:
            response = self._client.messages.create(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            draft_data = self._parse_response(response_text)

            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            # Merge sources
            all_sources = list(set(sources_used + draft_data.get("sources_used", [])))

            return DraftResult(
                subject=draft_data.get("subject", f"Re: {original_subject}"),
                body=draft_data.get("body", ""),
                confidence=float(draft_data.get("confidence", 0.5)),
                reasoning=draft_data.get("reasoning", ""),
                sources_used=all_sources,
                tokens_used=tokens_used,
                template_used=template_used,
                warnings=draft_data.get("warnings", []),
            )
        except Exception as e:
            self.logger.error("Draft generation failed", error=str(e))
            raise

    def _get_style_guidance(self, influencer_context: dict[str, Any] | None) -> str:
        """Get style guidance based on influencer relationship."""
        if not influencer_context:
            return "Use a professional, friendly tone suitable for a new contact."

        status = influencer_context.get("status", "prospect")
        risk_level = influencer_context.get("risk_level", "low")

        if risk_level == "high":
            return """CAUTION: This is a high-risk influencer.
- Be extra careful with commitments
- Document everything clearly
- Avoid any ambiguous language
- Suggest involving management for important decisions"""

        if status == "active":
            return """This is an active partner.
- Be friendly and efficient
- Reference past successful collaborations if known
- Show appreciation for the relationship
- Be direct about next steps"""

        if status == "blacklisted":
            return """WARNING: This influencer is blacklisted.
- Be polite but firm
- Do not discuss new opportunities
- Keep the response brief
- FLAG for human review immediately"""

        # Default for prospects
        return """This is a new or prospective influencer.
- Be welcoming and enthusiastic
- Establish a positive first impression
- Be thorough in explaining processes
- Offer to answer any questions"""

    def _parse_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM response."""
        response = response.strip()

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
            self.logger.warning("Failed to parse JSON, extracting body", response=response[:200])
            return {"body": response, "subject": "", "confidence": 0.3}

    async def generate_from_template(
        self,
        template: DraftTemplate,
        placeholders: dict[str, str],
        customize: bool = True,
    ) -> DraftResult:
        """Generate a draft from a template with optional customization."""
        # Fill in template
        subject = template.subject_template
        body = template.body_template

        for key, value in placeholders.items():
            subject = subject.replace(f"{{{key}}}", value)
            body = body.replace(f"{{{key}}}", value)

        # Remove unfilled optional placeholders
        for placeholder in template.optional_placeholders:
            body = body.replace(f"{{{placeholder}}}", "")

        # Clean up extra whitespace
        body = "\n".join(line for line in body.split("\n") if line.strip() or line == "")

        if customize:
            # Use LLM to polish the template
            polish_prompt = f"""Polish this email draft while keeping the core message:

Subject: {subject}

Body:
{body}

Guidelines:
- Keep the same structure and key points
- Make it sound natural, not templated
- Ensure the tone is {template.tone}
- Fix any awkward phrasing

Return as JSON with subject and body."""

            try:
                response = self._client.messages.create(
                    model="claude-haiku-4-20250514",  # Use faster model for polishing
                    max_tokens=1024,
                    temperature=0.5,
                    messages=[{"role": "user", "content": polish_prompt}],
                )

                polished = self._parse_response(response.content[0].text)
                subject = polished.get("subject", subject)
                body = polished.get("body", body)
            except Exception as e:
                self.logger.warning("Template polishing failed, using raw template", error=str(e))

        return DraftResult(
            subject=subject,
            body=body,
            confidence=0.8,
            reasoning=f"Generated from template: {template.name}",
            sources_used=[f"Template: {template.name}"],
            tokens_used=0,
            template_used=template.name,
        )

    async def improve_draft(
        self,
        current_draft: str,
        feedback: str,
        original_context: str | None = None,
    ) -> DraftResult:
        """Improve an existing draft based on feedback."""
        user_prompt = f"""Improve this email draft based on the feedback provided:

Current Draft:
{current_draft}

Feedback:
{feedback}

{"Original Context:\n" + original_context if original_context else ""}

Generate an improved version as a JSON object with subject, body, confidence, reasoning, sources_used, and warnings."""

        try:
            response = self._client.messages.create(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            draft_data = self._parse_response(response_text)
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            return DraftResult(
                subject=draft_data.get("subject", ""),
                body=draft_data.get("body", ""),
                confidence=float(draft_data.get("confidence", 0.7)),
                reasoning=draft_data.get("reasoning", f"Improved based on: {feedback}"),
                sources_used=[],
                tokens_used=tokens_used,
                warnings=draft_data.get("warnings", []),
            )
        except Exception as e:
            self.logger.error("Draft improvement failed", error=str(e))
            raise

    async def generate_initial_outreach(
        self,
        influencer_name: str,
        influencer_info: dict[str, Any],
        campaign_context: str,
        template: str | None = None,
    ) -> DraftResult:
        """Generate an initial outreach email for prospecting."""
        personalization_hints = []

        # Extract personalization opportunities
        if influencer_info.get("instagram_handle"):
            personalization_hints.append(f"Instagram: @{influencer_info['instagram_handle']}")
        if influencer_info.get("follower_count"):
            personalization_hints.append(f"Followers: {influencer_info['follower_count']:,}")
        if influencer_info.get("content_categories"):
            personalization_hints.append(f"Content focus: {influencer_info['content_categories']}")

        user_prompt = f"""Generate an initial outreach email for influencer prospecting:

Influencer: {influencer_name}
Info: {json.dumps(influencer_info, indent=2)}

Personalization opportunities:
{chr(10).join(f"- {hint}" for hint in personalization_hints)}

Campaign Context:
{campaign_context}

{"Template to follow:\n" + template if template else "Create a friendly, personalized outreach."}

Guidelines:
- Make it personal, not generic
- Reference something specific about their content
- Be clear about the opportunity
- Keep it concise (under 200 words)
- Include a clear call to action

Generate as JSON with subject, body, confidence, reasoning, sources_used, and warnings."""

        try:
            response = self._client.messages.create(
                model=settings.llm_model,
                max_tokens=settings.llm_max_tokens,
                temperature=0.8,  # Higher for creativity
                system=self.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            draft_data = self._parse_response(response_text)
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            return DraftResult(
                subject=draft_data.get("subject", f"Partnership Opportunity for {influencer_name}"),
                body=draft_data.get("body", ""),
                confidence=float(draft_data.get("confidence", 0.6)),
                reasoning=draft_data.get("reasoning", "Initial outreach for prospecting"),
                sources_used=draft_data.get("sources_used", []),
                tokens_used=tokens_used,
                template_used="initial_outreach",
                warnings=draft_data.get("warnings", []),
            )
        except Exception as e:
            self.logger.error("Initial outreach generation failed", error=str(e))
            raise

    async def explain_draft(self, draft: str, original_email: str) -> str:
        """Generate an explanation of why the draft was written this way."""
        prompt = f"""Explain this email draft in simple terms:

Original email received:
{original_email}

Draft response:
{draft}

Explain:
1. Why was this approach chosen?
2. What key points are addressed?
3. What is the expected outcome?

Keep it brief (2-3 sentences per point)."""

        try:
            response = self._client.messages.create(
                model="claude-haiku-4-20250514",
                max_tokens=500,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception:
            return "Explanation unavailable."
