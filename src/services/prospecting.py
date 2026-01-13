from __future__ import annotations

"""
Prospecting Service for automated initial outreach.

This module implements:
- Batch prospect loading from HubSpot
- Personalized initial outreach generation
- Campaign management and tracking
- Duplicate prevention
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from src.adapters.hubspot import HubSpotAdapter, HubSpotContact
from src.agents.triage import Intent
from src.core.config import settings
from src.core.database import get_session_context
from src.core.logging import get_logger
from src.core.redis import get_redis
from src.models import (
    Influencer,
    InfluencerStatus,
    Conversation,
    ConversationStatus,
    Message,
    MessageDirection,
    Task,
    TaskStatus,
    TaskType,
    AuditLog,
)
from src.services.drafting import DraftingService
from src.services.guardrail import GuardrailService
from src.services.rag import RAGService


class CampaignStatus(str, Enum):
    """Campaign status."""
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProspectStatus(str, Enum):
    """Prospect status within a campaign."""
    PENDING = "pending"
    DRAFT_GENERATED = "draft_generated"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class CampaignConfig:
    """Configuration for a prospecting campaign."""
    name: str
    description: str
    target_status: str = "prospect"  # HubSpot lifecycle stage filter
    target_tags: list[str] = field(default_factory=list)
    max_prospects: int = 100
    daily_limit: int = 20
    subject_template: str = "Collaboration opportunity with {brand_name}"
    personalization_fields: list[str] = field(default_factory=lambda: ["name", "instagram_handle", "follower_count"])
    auto_approve: bool = False  # Whether to auto-send (requires high confidence)
    confidence_threshold: float = 0.85
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProspectResult:
    """Result of processing a prospect."""
    prospect_id: UUID
    hubspot_id: str
    email: str
    status: ProspectStatus
    draft_subject: str | None = None
    draft_body: str | None = None
    confidence: float = 0.0
    error: str | None = None
    task_id: UUID | None = None


@dataclass
class CampaignProgress:
    """Progress of a campaign."""
    campaign_id: str
    total_prospects: int
    processed: int
    sent: int
    pending_approval: int
    failed: int
    skipped: int


class ProspectingService:
    """
    Service for managing prospecting campaigns.

    Handles:
    - Loading prospects from HubSpot
    - Generating personalized outreach drafts
    - Managing campaign progress
    - Rate limiting and duplicate prevention
    """

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self.hubspot = HubSpotAdapter()
        self.drafting_service = DraftingService()
        self.guardrail_service = GuardrailService()
        self.rag_service = RAGService()

    async def initialize(self) -> None:
        """Initialize all adapters."""
        await self.hubspot.initialize()
        await self.rag_service.initialize()

    async def close(self) -> None:
        """Close all adapters."""
        await self.hubspot.close()
        await self.rag_service.close()

    async def create_campaign(self, config: CampaignConfig) -> str:
        """Create a new prospecting campaign."""
        redis = await get_redis()
        import json

        campaign_id = str(uuid4())
        campaign_data = {
            "id": campaign_id,
            "name": config.name,
            "description": config.description,
            "target_status": config.target_status,
            "target_tags": config.target_tags,
            "max_prospects": config.max_prospects,
            "daily_limit": config.daily_limit,
            "subject_template": config.subject_template,
            "personalization_fields": config.personalization_fields,
            "auto_approve": config.auto_approve,
            "confidence_threshold": config.confidence_threshold,
            "status": CampaignStatus.DRAFT.value,
            "created_at": config.created_at.isoformat(),
            "prospects_loaded": 0,
            "prospects_processed": 0,
            "prospects_sent": 0,
        }

        await redis.set(
            f"campaign:{campaign_id}",
            json.dumps(campaign_data),
            ex=86400 * 30,  # 30 days
        )

        self.logger.info("Campaign created", campaign_id=campaign_id, name=config.name)
        return campaign_id

    async def load_prospects_for_campaign(
        self,
        campaign_id: str,
        lifecycle_stage: str = "lead",
    ) -> list[HubSpotContact]:
        """Load prospects from HubSpot for a campaign."""
        redis = await get_redis()
        import json

        # Get campaign config
        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            raise ValueError(f"Campaign {campaign_id} not found")

        config = json.loads(campaign_data)
        max_prospects = config["max_prospects"]

        # Get already contacted emails to avoid duplicates
        contacted_emails = await self._get_contacted_emails()

        # Load from HubSpot
        all_contacts = []
        after = None

        while len(all_contacts) < max_prospects:
            contacts, next_page = await self.hubspot.list_contacts(
                limit=100,
                after=after,
            )

            for contact in contacts:
                if contact.email and contact.email not in contacted_emails:
                    # Check if this is a prospect (not yet contacted)
                    if contact.lifecycle_stage == lifecycle_stage:
                        all_contacts.append(contact)
                        if len(all_contacts) >= max_prospects:
                            break

            if not next_page:
                break
            after = next_page

        # Store prospects for campaign
        prospect_ids = []
        for contact in all_contacts:
            prospect_key = f"campaign:{campaign_id}:prospect:{contact.hubspot_id}"
            await redis.set(
                prospect_key,
                json.dumps({
                    "hubspot_id": contact.hubspot_id,
                    "email": contact.email,
                    "first_name": contact.first_name,
                    "last_name": contact.last_name,
                    "company": contact.company,
                    "status": ProspectStatus.PENDING.value,
                }),
                ex=86400 * 30,
            )
            prospect_ids.append(contact.hubspot_id)

        # Update campaign
        config["prospects_loaded"] = len(all_contacts)
        config["prospect_ids"] = prospect_ids
        await redis.set(f"campaign:{campaign_id}", json.dumps(config), ex=86400 * 30)

        self.logger.info(
            "Prospects loaded for campaign",
            campaign_id=campaign_id,
            count=len(all_contacts),
        )

        return all_contacts

    async def _get_contacted_emails(self) -> set[str]:
        """Get set of emails we've already contacted."""
        contacted = set()

        async with get_session_context() as session:
            from sqlalchemy import select

            # Get all influencers with outbound messages
            result = await session.execute(
                select(Influencer.email)
                .join(Conversation)
                .join(Message)
                .where(Message.direction == MessageDirection.OUTBOUND)
                .distinct()
            )
            for row in result:
                contacted.add(row[0])

        return contacted

    async def generate_outreach_draft(
        self,
        campaign_id: str,
        prospect: HubSpotContact,
    ) -> ProspectResult:
        """Generate personalized outreach draft for a prospect."""
        self.logger.info(
            "Generating outreach draft",
            campaign_id=campaign_id,
            prospect_email=prospect.email,
        )

        redis = await get_redis()
        import json

        # Get campaign config
        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            return ProspectResult(
                prospect_id=uuid4(),
                hubspot_id=prospect.hubspot_id,
                email=prospect.email,
                status=ProspectStatus.FAILED,
                error="Campaign not found",
            )

        config = json.loads(campaign_data)

        # Get outreach SOP/template
        sop_results = await self.rag_service.search_sops(
            query="initial outreach cold email prospect collaboration",
            limit=3,
        )
        sop_content = "\n\n".join([r.content for r in sop_results])

        # Build personalization context
        prospect_name = f"{prospect.first_name or ''} {prospect.last_name or ''}".strip()
        if not prospect_name:
            prospect_name = prospect.email.split("@")[0].replace(".", " ").title()

        personalization = {
            "name": prospect_name,
            "first_name": prospect.first_name or prospect_name.split()[0],
            "company": prospect.company,
            "email": prospect.email,
        }

        # Add any custom properties from HubSpot
        if prospect.properties:
            for field in config.get("personalization_fields", []):
                if field in prospect.properties:
                    personalization[field] = prospect.properties[field]

        # Generate subject
        subject = config["subject_template"].format(
            brand_name=settings.brand_name if hasattr(settings, 'brand_name') else "our brand",
            **personalization,
        )

        # Generate draft
        draft_result = await self.drafting_service.generate_draft(
            intent=Intent.COLLAB_INQUIRY,
            original_email="",  # No original email for cold outreach
            original_subject="",
            sender_name=prospect_name,
            sop_content=sop_content,
            influencer_context={
                "name": prospect_name,
                "status": "prospect",
                "is_cold_outreach": True,
                **personalization,
            },
            additional_context=f"""
This is a COLD OUTREACH email to a potential influencer partner.
Personalization data:
- Name: {prospect_name}
- Company: {prospect.company or 'Unknown'}

Write a warm, personalized initial outreach email that:
1. Is friendly but professional
2. Briefly introduces our brand and collaboration interest
3. References something specific about them if possible
4. Has a clear but soft call-to-action
5. Is concise (under 150 words)
""",
        )

        # Check guardrails
        guardrail_result = await self.guardrail_service.check_content(
            draft_result.body,
            check_pii=True,
            check_financial=True,
            check_policy=True,
        )

        if not guardrail_result.passed:
            self.logger.warning(
                "Draft failed guardrails",
                prospect_email=prospect.email,
                violations=[v.value for v in guardrail_result.violations],
            )
            return ProspectResult(
                prospect_id=uuid4(),
                hubspot_id=prospect.hubspot_id,
                email=prospect.email,
                status=ProspectStatus.FAILED,
                error=f"Guardrail violations: {[v.value for v in guardrail_result.violations]}",
            )

        # Create influencer and task in database
        async with get_session_context() as session:
            from sqlalchemy import select

            # Find or create influencer
            result = await session.execute(
                select(Influencer).where(Influencer.email == prospect.email)
            )
            influencer = result.scalar_one_or_none()

            if not influencer:
                influencer = Influencer(
                    name=prospect_name,
                    email=prospect.email,
                    hubspot_id=prospect.hubspot_id,
                    status=InfluencerStatus.PROSPECT,
                )
                session.add(influencer)
                await session.flush()

            # Create conversation
            conversation = Conversation(
                influencer_id=influencer.id,
                subject=subject,
                status=ConversationStatus.OPEN,
                primary_intent=Intent.COLLAB_INQUIRY.value,
            )
            session.add(conversation)
            await session.flush()

            # Determine task status based on auto-approve setting
            if config["auto_approve"] and draft_result.confidence >= config["confidence_threshold"]:
                task_status = TaskStatus.APPROVED
                prospect_status = ProspectStatus.APPROVED
            else:
                task_status = TaskStatus.AWAITING_APPROVAL
                prospect_status = ProspectStatus.AWAITING_APPROVAL

            # Create task
            task = Task(
                conversation_id=conversation.id,
                type=TaskType.INITIAL_OUTREACH,
                status=task_status,
                draft_subject=subject,
                draft_content=draft_result.body,
                confidence_score=draft_result.confidence,
                intent_detected=Intent.COLLAB_INQUIRY.value,
                context_used={
                    "campaign_id": campaign_id,
                    "personalization": personalization,
                    "sources": draft_result.sources_used,
                },
            )
            session.add(task)

            if task_status == TaskStatus.APPROVED:
                task.approval_user_id = "campaign_auto"
                task.approval_timestamp = datetime.utcnow()

            await session.flush()
            task_id = task.id

            # Create audit log
            audit_log = AuditLog(
                task_id=task.id,
                action="outreach_draft_generated",
                actor="prospecting_service",
                actor_type="agent",
                timestamp=datetime.utcnow(),
                metadata={
                    "campaign_id": campaign_id,
                    "prospect_email": prospect.email,
                    "confidence": draft_result.confidence,
                    "auto_approved": task_status == TaskStatus.APPROVED,
                },
            )
            session.add(audit_log)

        # Update prospect status in Redis
        prospect_key = f"campaign:{campaign_id}:prospect:{prospect.hubspot_id}"
        await redis.set(
            prospect_key,
            json.dumps({
                "hubspot_id": prospect.hubspot_id,
                "email": prospect.email,
                "first_name": prospect.first_name,
                "last_name": prospect.last_name,
                "company": prospect.company,
                "status": prospect_status.value,
                "task_id": str(task_id),
                "confidence": draft_result.confidence,
            }),
            ex=86400 * 30,
        )

        return ProspectResult(
            prospect_id=influencer.id,
            hubspot_id=prospect.hubspot_id,
            email=prospect.email,
            status=prospect_status,
            draft_subject=subject,
            draft_body=draft_result.body,
            confidence=draft_result.confidence,
            task_id=task_id,
        )

    async def process_campaign_batch(
        self,
        campaign_id: str,
        batch_size: int = 10,
    ) -> list[ProspectResult]:
        """Process a batch of prospects for a campaign."""
        redis = await get_redis()
        import json

        # Get campaign config
        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            raise ValueError(f"Campaign {campaign_id} not found")

        config = json.loads(campaign_data)

        if config["status"] != CampaignStatus.ACTIVE.value:
            raise ValueError(f"Campaign {campaign_id} is not active")

        # Check daily limit
        today_key = f"campaign:{campaign_id}:daily:{datetime.utcnow().date().isoformat()}"
        daily_count = await redis.get(today_key)
        daily_count = int(daily_count) if daily_count else 0

        if daily_count >= config["daily_limit"]:
            self.logger.info(
                "Daily limit reached",
                campaign_id=campaign_id,
                limit=config["daily_limit"],
            )
            return []

        remaining = min(batch_size, config["daily_limit"] - daily_count)

        # Get pending prospects
        prospect_ids = config.get("prospect_ids", [])
        results = []

        for hubspot_id in prospect_ids:
            if len(results) >= remaining:
                break

            prospect_key = f"campaign:{campaign_id}:prospect:{hubspot_id}"
            prospect_data = await redis.get(prospect_key)
            if not prospect_data:
                continue

            prospect_info = json.loads(prospect_data)
            if prospect_info["status"] != ProspectStatus.PENDING.value:
                continue

            # Load full contact from HubSpot
            contact = await self.hubspot.get_contact(hubspot_id)
            if not contact:
                continue

            # Generate draft
            result = await self.generate_outreach_draft(campaign_id, contact)
            results.append(result)

            # Update daily count
            await redis.incr(today_key)
            await redis.expire(today_key, 86400)

        # Update campaign progress
        config["prospects_processed"] = config.get("prospects_processed", 0) + len(results)
        await redis.set(f"campaign:{campaign_id}", json.dumps(config), ex=86400 * 30)

        self.logger.info(
            "Campaign batch processed",
            campaign_id=campaign_id,
            processed=len(results),
        )

        return results

    async def get_campaign_progress(self, campaign_id: str) -> CampaignProgress:
        """Get progress for a campaign."""
        redis = await get_redis()
        import json

        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            raise ValueError(f"Campaign {campaign_id} not found")

        config = json.loads(campaign_data)
        prospect_ids = config.get("prospect_ids", [])

        # Count statuses
        status_counts = {
            ProspectStatus.PENDING.value: 0,
            ProspectStatus.AWAITING_APPROVAL.value: 0,
            ProspectStatus.SENT.value: 0,
            ProspectStatus.FAILED.value: 0,
            ProspectStatus.SKIPPED.value: 0,
        }

        for hubspot_id in prospect_ids:
            prospect_key = f"campaign:{campaign_id}:prospect:{hubspot_id}"
            prospect_data = await redis.get(prospect_key)
            if prospect_data:
                prospect_info = json.loads(prospect_data)
                status = prospect_info.get("status", ProspectStatus.PENDING.value)
                if status in status_counts:
                    status_counts[status] += 1

        return CampaignProgress(
            campaign_id=campaign_id,
            total_prospects=len(prospect_ids),
            processed=config.get("prospects_processed", 0),
            sent=status_counts[ProspectStatus.SENT.value],
            pending_approval=status_counts[ProspectStatus.AWAITING_APPROVAL.value],
            failed=status_counts[ProspectStatus.FAILED.value],
            skipped=status_counts[ProspectStatus.SKIPPED.value],
        )

    async def start_campaign(self, campaign_id: str) -> None:
        """Start a campaign (set to active)."""
        redis = await get_redis()
        import json

        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            raise ValueError(f"Campaign {campaign_id} not found")

        config = json.loads(campaign_data)
        config["status"] = CampaignStatus.ACTIVE.value
        config["started_at"] = datetime.utcnow().isoformat()

        await redis.set(f"campaign:{campaign_id}", json.dumps(config), ex=86400 * 30)
        self.logger.info("Campaign started", campaign_id=campaign_id)

    async def pause_campaign(self, campaign_id: str) -> None:
        """Pause a campaign."""
        redis = await get_redis()
        import json

        campaign_data = await redis.get(f"campaign:{campaign_id}")
        if not campaign_data:
            raise ValueError(f"Campaign {campaign_id} not found")

        config = json.loads(campaign_data)
        config["status"] = CampaignStatus.PAUSED.value
        config["paused_at"] = datetime.utcnow().isoformat()

        await redis.set(f"campaign:{campaign_id}", json.dumps(config), ex=86400 * 30)
        self.logger.info("Campaign paused", campaign_id=campaign_id)

    async def list_campaigns(self) -> list[dict[str, Any]]:
        """List all campaigns."""
        redis = await get_redis()
        import json

        campaigns = []
        # Scan for campaign keys
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="campaign:*", count=100)
            for key in keys:
                # Skip prospect keys
                if ":prospect:" in key or ":daily:" in key:
                    continue
                data = await redis.get(key)
                if data:
                    campaigns.append(json.loads(data))
            if cursor == 0:
                break

        return sorted(campaigns, key=lambda x: x.get("created_at", ""), reverse=True)


# Singleton instance
_prospecting_service: ProspectingService | None = None


async def get_prospecting_service() -> ProspectingService:
    """Get the prospecting service singleton."""
    global _prospecting_service
    if _prospecting_service is None:
        _prospecting_service = ProspectingService()
        await _prospecting_service.initialize()
    return _prospecting_service
