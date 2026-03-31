import asyncio
from datetime import datetime
from uuid import UUID

from celery import shared_task

from src.adapters.gmail import GmailAdapter
from src.adapters.hubspot import HubSpotAdapter
from src.adapters.slack import ApprovalRequest, SlackAdapter
from src.agents.triage import Intent, Priority, Sentiment, TriageAgent
from src.core.database import get_session_context
from src.core.logging import get_logger
from src.core.redis import check_kill_switch
from src.models import (
    AuditLog,
    Conversation,
    ConversationStatus,
    Influencer,
    InfluencerStatus,
    Message,
    MessageDirection,
    Task,
    TaskStatus,
    TaskType,
)
from src.services.autopilot import (
    AutopilotDecision,
    get_autopilot_engine,
)
from src.services.drafting import DraftingService
from src.services.guardrail import GuardrailService
from src.services.rag import RAGService

logger = get_logger(__name__)


def run_async(coro):
    """Helper to run async code in sync context."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task(bind=True, max_retries=3)
def poll_gmail(self):
    """Poll Gmail for new emails."""
    return run_async(_poll_gmail_async())


async def _poll_gmail_async():
    """Async implementation of Gmail polling."""
    if await check_kill_switch():
        logger.warning("Kill switch active, skipping Gmail poll")
        return {"status": "skipped", "reason": "kill_switch"}

    gmail = GmailAdapter()
    await gmail.initialize()

    try:
        messages = await gmail.list_messages(query="is:unread", max_results=10)
        processed = 0

        for msg in messages:
            # Process each email
            process_incoming_email.delay(
                message_id=msg.message_id,
                thread_id=msg.thread_id,
                subject=msg.subject,
                sender=msg.sender,
                body=msg.body,
                timestamp=msg.timestamp.isoformat(),
            )
            processed += 1

        return {"status": "success", "processed": processed}
    finally:
        await gmail.close()


@shared_task(bind=True, max_retries=3)
def process_incoming_email(
    self,
    message_id: str,
    thread_id: str,
    subject: str,
    sender: str,
    body: str,
    timestamp: str,
):
    """Process an incoming email."""
    return run_async(
        _process_incoming_email_async(
            message_id, thread_id, subject, sender, body, timestamp
        )
    )


async def _process_incoming_email_async(
    message_id: str,
    thread_id: str,
    subject: str,
    sender: str,
    body: str,
    timestamp: str,
):
    """Async implementation of email processing."""
    if await check_kill_switch():
        logger.warning("Kill switch active, skipping email processing")
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Processing email", message_id=message_id, sender=sender)

    # Triage the email
    triage_agent = TriageAgent()
    triage_result = await triage_agent.analyze(
        email_body=body,
        email_subject=subject,
        sender=sender,
    )

    async with get_session_context() as session:
        # Find or create influencer
        from sqlalchemy import select

        result = await session.execute(
            select(Influencer).where(Influencer.email == sender)
        )
        influencer = result.scalar_one_or_none()

        if not influencer:
            # Create new influencer
            name = sender.split("@")[0].replace(".", " ").title()
            influencer = Influencer(
                name=name,
                email=sender,
            )
            session.add(influencer)
            await session.flush()

        # Find or create conversation
        result = await session.execute(
            select(Conversation).where(Conversation.thread_id == thread_id)
        )
        conversation = result.scalar_one_or_none()

        if not conversation:
            conversation = Conversation(
                influencer_id=influencer.id,
                thread_id=thread_id,
                subject=subject,
                primary_intent=triage_result.intent.value,
                sentiment=triage_result.sentiment.value,
                priority=triage_result.priority.value,
                status=ConversationStatus.NEEDS_ACTION,
            )
            session.add(conversation)
            await session.flush()

        # Create message
        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content=body,
            timestamp=datetime.fromisoformat(timestamp),
            message_id=message_id,
            subject=subject,
            sender=sender,
        )
        session.add(message)

        # Update conversation
        conversation.last_message_at = datetime.fromisoformat(timestamp)
        conversation.primary_intent = triage_result.intent.value

        # Create task
        task = Task(
            conversation_id=conversation.id,
            type=TaskType.DRAFT_REPLY,
            status=TaskStatus.PENDING,
            confidence_score=triage_result.confidence,
            intent_detected=triage_result.intent.value,
        )
        session.add(task)
        await session.flush()

        task_id = str(task.id)

    # If not spam and action needed, generate draft
    if triage_result.intent != Intent.SPAM:
        generate_draft_task.delay(task_id)

    # Post notification to Slack
    slack = SlackAdapter()
    await slack.initialize()
    try:
        await slack.post_new_email_alert(
            influencer_name=influencer.name,
            influencer_email=sender,
            subject=subject,
            preview=body[:500],
            priority=triage_result.priority.value,
        )
    finally:
        await slack.close()

    return {
        "status": "success",
        "intent": triage_result.intent.value,
        "confidence": triage_result.confidence,
        "task_id": task_id,
    }


@shared_task(bind=True, max_retries=3)
def generate_draft_task(self, task_id: str):
    """Generate a draft for a task."""
    return run_async(_generate_draft_async(task_id))


async def _generate_draft_async(task_id: str):
    """Async implementation of draft generation with autopilot support."""
    if await check_kill_switch():
        logger.warning("Kill switch active, skipping draft generation")
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Generating draft", task_id=task_id)

    async with get_session_context() as session:
        from sqlalchemy import func, select

        # Get task with conversation and messages
        result = await session.execute(
            select(Task).where(Task.id == UUID(task_id))
        )
        task = result.scalar_one_or_none()

        if not task:
            return {"status": "error", "reason": "task_not_found"}

        # Get conversation
        result = await session.execute(
            select(Conversation).where(Conversation.id == task.conversation_id)
        )
        conversation = result.scalar_one_or_none()

        # Get influencer
        result = await session.execute(
            select(Influencer).where(Influencer.id == conversation.influencer_id)
        )
        influencer = result.scalar_one_or_none()

        # Get latest message
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.timestamp.desc())
            .limit(1)
        )
        latest_message = result.scalar_one_or_none()

        if not latest_message:
            return {"status": "error", "reason": "no_messages"}

        # Get thread length
        result = await session.execute(
            select(func.count(Message.id))
            .where(Message.conversation_id == conversation.id)
        )
        thread_length = result.scalar() or 0

        # Get SOP context
        rag_service = RAGService()
        await rag_service.initialize()
        try:
            sop_results = await rag_service.search_sops(
                query=latest_message.content[:500],
                limit=5,
            )
            sop_content = "\n\n".join([r.content for r in sop_results])
            sop_sources = [r.source for r in sop_results]
        finally:
            await rag_service.close()

        # Generate draft
        drafting_service = DraftingService()
        intent = Intent(task.intent_detected) if task.intent_detected else Intent.GENERAL_QUESTION
        sentiment = Sentiment(conversation.sentiment) if conversation.sentiment else Sentiment.NEUTRAL
        priority = Priority(conversation.priority) if conversation.priority else Priority.MEDIUM

        draft_result = await drafting_service.generate_draft(
            intent=intent,
            original_email=latest_message.content,
            original_subject=latest_message.subject or conversation.subject or "",
            sender_name=influencer.name if influencer else "Influencer",
            sop_content=sop_content,
            influencer_context={
                "name": influencer.name,
                "status": influencer.status.value,
                "risk_level": influencer.risk_level.value if influencer.risk_level else "low",
            } if influencer else None,
        )

        # Check guardrails
        guardrail_service = GuardrailService()
        guardrail_result = await guardrail_service.check_content(
            draft_result.body,
            check_pii=True,
            check_financial=True,
            check_policy=True,
        )

        # Get autopilot decision
        autopilot_engine = get_autopilot_engine()
        autopilot_result = await autopilot_engine.make_decision(
            intent=intent,
            triage_confidence=task.confidence_score or 0.5,
            sentiment=sentiment,
            priority=priority,
            influencer_status=influencer.status.value if influencer else None,
            influencer_risk=influencer.risk_level.value if influencer and influencer.risk_level else None,
            draft_content=draft_result.body,
            draft_confidence=draft_result.confidence,
            sop_sources=sop_sources,
            template_used=draft_result.template_used if hasattr(draft_result, 'template_used') else False,
            thread_length=thread_length,
        )

        # Record autopilot decision for audit
        await autopilot_engine.record_decision(
            task_id=UUID(task_id),
            result=autopilot_result,
            conversation_id=conversation.id,
        )

        # Update task
        task.draft_subject = draft_result.subject
        task.draft_content = draft_result.body
        task.confidence_score = draft_result.confidence
        task.context_used = {
            "sources": sop_sources,
            "guardrail_passed": guardrail_result.passed,
            "guardrail_violations": [v.value for v in guardrail_result.violations],
            "autopilot_decision": autopilot_result.decision.value,
            "autopilot_confidence": autopilot_result.confidence.overall,
            "autopilot_reason": autopilot_result.reason,
        }

        # Handle autopilot decision
        if autopilot_result.can_auto_send:
            # Auto-approve and send
            task.status = TaskStatus.APPROVED
            task.approval_user_id = "autopilot"
            task.approval_timestamp = datetime.utcnow()
            await session.flush()

            logger.info(
                "Autopilot auto-approved",
                task_id=task_id,
                confidence=autopilot_result.confidence.overall,
                intent=intent.value,
            )

            # Create audit log for auto-approval
            audit_log = AuditLog(
                task_id=task.id,
                action="autopilot_approved",
                actor="autopilot",
                actor_type="agent",
                timestamp=datetime.utcnow(),
                extra_data={
                    "confidence": autopilot_result.confidence.to_dict(),
                    "reason": autopilot_result.reason,
                },
            )
            session.add(audit_log)
            await session.flush()

            # Trigger email send
            send_email_task.delay(task_id)

            return {
                "status": "auto_sent",
                "draft_confidence": draft_result.confidence,
                "autopilot_confidence": autopilot_result.confidence.overall,
                "guardrail_passed": guardrail_result.passed,
            }

        elif autopilot_result.decision == AutopilotDecision.ESCALATE:
            task.status = TaskStatus.AWAITING_APPROVAL
            await session.flush()

            # Notify for senior review
            slack = SlackAdapter()
            await slack.initialize()
            try:
                approval_request = ApprovalRequest(
                    task_id=task_id,
                    conversation_id=str(conversation.id),
                    influencer_name=influencer.name if influencer else "Unknown",
                    influencer_email=influencer.email if influencer else "",
                    intent=task.intent_detected or "unknown",
                    draft_subject=draft_result.subject,
                    draft_content=draft_result.body,
                    context_summary=f"⚠️ ESCALATED: {autopilot_result.reason}",
                    confidence_score=autopilot_result.confidence.overall,
                )
                slack_msg = await slack.post_approval_request(approval_request)
                task.slack_message_ts = slack_msg.ts
                task.slack_channel_id = slack_msg.channel
            finally:
                await slack.close()

            return {
                "status": "escalated",
                "reason": autopilot_result.reason,
                "draft_confidence": draft_result.confidence,
            }

        elif autopilot_result.decision == AutopilotDecision.BLOCK:
            task.status = TaskStatus.REJECTED
            task.rejection_reason = autopilot_result.reason
            await session.flush()

            logger.warning(
                "Autopilot blocked",
                task_id=task_id,
                reason=autopilot_result.reason,
                blocked_reasons=autopilot_result.blocked_reasons,
            )

            return {
                "status": "blocked",
                "reason": autopilot_result.reason,
                "blocked_reasons": autopilot_result.blocked_reasons,
            }

        else:
            # Human review required (default copilot behavior)
            task.status = TaskStatus.AWAITING_APPROVAL
            await session.flush()

    # Post to Slack for approval (copilot mode)
    slack = SlackAdapter()
    await slack.initialize()
    try:
        approval_request = ApprovalRequest(
            task_id=task_id,
            conversation_id=str(conversation.id),
            influencer_name=influencer.name if influencer else "Unknown",
            influencer_email=influencer.email if influencer else "",
            intent=task.intent_detected or "unknown",
            draft_subject=draft_result.subject,
            draft_content=draft_result.body,
            context_summary=f"Based on {len(sop_sources)} SOPs | {autopilot_result.reason}",
            confidence_score=draft_result.confidence,
        )
        slack_msg = await slack.post_approval_request(approval_request)

        # Update task with Slack message info
        async with get_session_context() as session:
            result = await session.execute(
                select(Task).where(Task.id == UUID(task_id))
            )
            task = result.scalar_one_or_none()
            if task:
                task.slack_message_ts = slack_msg.ts
                task.slack_channel_id = slack_msg.channel
    finally:
        await slack.close()

    return {
        "status": "awaiting_approval",
        "draft_confidence": draft_result.confidence,
        "autopilot_decision": autopilot_result.decision.value,
        "guardrail_passed": guardrail_result.passed,
    }


@shared_task(bind=True, max_retries=3)
def send_email_task(self, task_id: str):
    """Send an approved email."""
    return run_async(_send_email_async(task_id))


async def _send_email_async(task_id: str):
    """Async implementation of email sending."""
    if await check_kill_switch():
        logger.warning("Kill switch active, skipping email send")
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Sending email", task_id=task_id)

    async with get_session_context() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Task).where(Task.id == UUID(task_id))
        )
        task = result.scalar_one_or_none()

        if not task or task.status != TaskStatus.APPROVED:
            return {"status": "error", "reason": "invalid_task_status"}

        # Get conversation and influencer
        result = await session.execute(
            select(Conversation).where(Conversation.id == task.conversation_id)
        )
        conversation = result.scalar_one_or_none()

        result = await session.execute(
            select(Influencer).where(Influencer.id == conversation.influencer_id)
        )
        influencer = result.scalar_one_or_none()

        # Send email
        gmail = GmailAdapter()
        await gmail.initialize()
        try:
            message_id = await gmail.send_message(
                to=influencer.email,
                subject=task.draft_subject or f"Re: {conversation.subject}",
                body=task.draft_content,
                thread_id=conversation.thread_id,
            )

            # Update task
            task.status = TaskStatus.SENT

            # Create outbound message
            message = Message(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=task.draft_content,
                timestamp=datetime.utcnow(),
                message_id=message_id,
                subject=task.draft_subject,
            )
            session.add(message)

            # Update conversation
            conversation.status = ConversationStatus.WAITING_REPLY
            conversation.last_message_at = datetime.utcnow()

            # Create audit log
            audit_log = AuditLog(
                task_id=task.id,
                action="email_sent",
                actor="system",
                actor_type="agent",
                timestamp=datetime.utcnow(),
                extra_data={"gmail_message_id": message_id},
            )
            session.add(audit_log)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            task.retry_count += 1
            raise
        finally:
            await gmail.close()

    return {"status": "success", "message_id": message_id}


@shared_task
def sync_hubspot_task():
    """Sync contacts from HubSpot."""
    return run_async(_sync_hubspot_async())


async def _sync_hubspot_async():
    """Async implementation of HubSpot sync."""
    logger.info("Syncing HubSpot contacts")

    hubspot = HubSpotAdapter()
    await hubspot.initialize()

    try:
        synced = 0
        after = None

        while True:
            contacts, next_page = await hubspot.list_contacts(limit=100, after=after)

            async with get_session_context() as session:
                from sqlalchemy import select

                for contact in contacts:
                    result = await session.execute(
                        select(Influencer).where(
                            Influencer.hubspot_id == contact.hubspot_id
                        )
                    )
                    influencer = result.scalar_one_or_none()

                    if influencer:
                        # Update existing
                        influencer.name = f"{contact.first_name or ''} {contact.last_name or ''}".strip() or influencer.name
                    else:
                        # Create new
                        influencer = Influencer(
                            hubspot_id=contact.hubspot_id,
                            name=f"{contact.first_name or ''} {contact.last_name or ''}".strip() or contact.email.split("@")[0],
                            email=contact.email,
                        )
                        session.add(influencer)

                    synced += 1

            if not next_page:
                break
            after = next_page

        logger.info("HubSpot sync complete", synced=synced)
        return {"status": "success", "synced": synced}
    finally:
        await hubspot.close()


@shared_task
def sync_notion_task():
    """Sync SOPs from Notion."""
    return run_async(_sync_notion_async())


async def _sync_notion_async():
    """Async implementation of Notion sync."""
    logger.info("Syncing Notion SOPs")

    rag_service = RAGService()
    await rag_service.initialize()

    try:
        count = await rag_service.sync_sops_from_notion()
        return {"status": "success", "synced": count}
    finally:
        await rag_service.close()


@shared_task
def check_pending_reminders():
    """Check for conversations needing follow-up."""
    return run_async(_check_pending_reminders_async())


async def _check_pending_reminders_async():
    """Async implementation of reminder check."""
    if await check_kill_switch():
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Checking pending reminders")

    # This would check for conversations waiting for reply
    # and create reminder tasks if needed

    return {"status": "success"}


# =============================================================================
# Prospecting Tasks
# =============================================================================

@shared_task(bind=True, max_retries=3)
def process_prospecting_campaign(self, campaign_id: str, batch_size: int = 10):
    """Process a batch of prospects for a campaign."""
    return run_async(_process_prospecting_campaign_async(campaign_id, batch_size))


async def _process_prospecting_campaign_async(campaign_id: str, batch_size: int):
    """Async implementation of campaign processing."""
    from src.services.prospecting import get_prospecting_service

    if await check_kill_switch():
        logger.warning("Kill switch active, skipping prospecting")
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Processing prospecting campaign", campaign_id=campaign_id)

    service = await get_prospecting_service()
    try:
        results = await service.process_campaign_batch(campaign_id, batch_size)
        return {
            "status": "success",
            "processed": len(results),
            "results": [
                {
                    "email": r.email,
                    "status": r.status.value,
                    "confidence": r.confidence,
                    "task_id": str(r.task_id) if r.task_id else None,
                }
                for r in results
            ],
        }
    except ValueError as e:
        return {"status": "error", "reason": str(e)}


@shared_task
def run_active_campaigns():
    """Run all active prospecting campaigns (scheduled task)."""
    return run_async(_run_active_campaigns_async())


async def _run_active_campaigns_async():
    """Async implementation of running active campaigns."""
    from src.services.prospecting import CampaignStatus, get_prospecting_service

    if await check_kill_switch():
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Running active prospecting campaigns")

    service = await get_prospecting_service()
    campaigns = await service.list_campaigns()

    processed_campaigns = 0
    total_processed = 0

    for campaign in campaigns:
        if campaign.get("status") == CampaignStatus.ACTIVE.value:
            campaign_id = campaign["id"]
            try:
                results = await service.process_campaign_batch(campaign_id, batch_size=10)
                processed_campaigns += 1
                total_processed += len(results)
            except Exception as e:
                logger.error(
                    "Error processing campaign",
                    campaign_id=campaign_id,
                    error=str(e),
                )

    return {
        "status": "success",
        "campaigns_processed": processed_campaigns,
        "prospects_processed": total_processed,
    }


@shared_task(bind=True, max_retries=3)
def send_approved_outreach(self, task_id: str):
    """Send an approved outreach email."""
    return run_async(_send_approved_outreach_async(task_id))


async def _send_approved_outreach_async(task_id: str):
    """Async implementation of sending approved outreach."""
    if await check_kill_switch():
        logger.warning("Kill switch active, skipping outreach send")
        return {"status": "skipped", "reason": "kill_switch"}

    logger.info("Sending approved outreach", task_id=task_id)

    async with get_session_context() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(Task).where(Task.id == UUID(task_id))
        )
        task = result.scalar_one_or_none()

        if not task or task.status != TaskStatus.APPROVED:
            return {"status": "error", "reason": "invalid_task_status"}

        if task.type != TaskType.INITIAL_OUTREACH:
            return {"status": "error", "reason": "not_outreach_task"}

        # Get conversation and influencer
        result = await session.execute(
            select(Conversation).where(Conversation.id == task.conversation_id)
        )
        conversation = result.scalar_one_or_none()

        result = await session.execute(
            select(Influencer).where(Influencer.id == conversation.influencer_id)
        )
        influencer = result.scalar_one_or_none()

        # Send email
        gmail = GmailAdapter()
        await gmail.initialize()
        try:
            message_id = await gmail.send_message(
                to=influencer.email,
                subject=task.draft_subject,
                body=task.draft_content,
            )

            # Update task
            task.status = TaskStatus.SENT

            # Create outbound message
            message = Message(
                conversation_id=conversation.id,
                direction=MessageDirection.OUTBOUND,
                content=task.draft_content,
                timestamp=datetime.utcnow(),
                message_id=message_id,
                subject=task.draft_subject,
            )
            session.add(message)

            # Update conversation
            conversation.status = ConversationStatus.WAITING_REPLY
            conversation.last_message_at = datetime.utcnow()
            conversation.thread_id = message_id  # Use first message as thread ID

            # Update influencer status
            if influencer.status == InfluencerStatus.PROSPECT:
                influencer.status = InfluencerStatus.ACTIVE

            # Create audit log
            audit_log = AuditLog(
                task_id=task.id,
                action="outreach_sent",
                actor="system",
                actor_type="agent",
                timestamp=datetime.utcnow(),
                extra_data={
                    "gmail_message_id": message_id,
                    "campaign_id": task.context_used.get("campaign_id") if task.context_used else None,
                },
            )
            session.add(audit_log)

            # Update Redis prospect status if campaign
            if task.context_used and "campaign_id" in task.context_used:
                redis = await get_redis()
                import json

                from src.services.prospecting import ProspectStatus

                campaign_id = task.context_used["campaign_id"]
                prospect_key = f"campaign:{campaign_id}:prospect:{influencer.hubspot_id}"
                prospect_data = await redis.get(prospect_key)
                if prospect_data:
                    prospect_info = json.loads(prospect_data)
                    prospect_info["status"] = ProspectStatus.SENT.value
                    await redis.set(prospect_key, json.dumps(prospect_info), ex=86400 * 30)

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            task.retry_count += 1
            raise
        finally:
            await gmail.close()

    return {"status": "success", "message_id": message_id}
