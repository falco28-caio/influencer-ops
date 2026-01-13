from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adapters.gmail import GmailAdapter
from src.adapters.slack import SlackAdapter, ApprovalRequest
from src.agents.triage import TriageAgent
from src.api.schemas import (
    ConversationCreate,
    ConversationResponse,
    DraftRequest,
    DraftResponse,
    EmailTriageRequest,
    EmailTriageResponse,
    HealthResponse,
    InfluencerCreate,
    InfluencerResponse,
    InfluencerUpdate,
    KillSwitchRequest,
    KillSwitchResponse,
    SlackInteractionPayload,
    SyncRequest,
    SyncResponse,
    TaskApproval,
    TaskResponse,
    CampaignCreateRequest,
    CampaignResponse,
    CampaignProgressResponse,
    CampaignBatchResponse,
    ProspectResultResponse,
    AutopilotStatsResponse,
)
from src.core.config import settings
from src.core.database import get_session
from src.core.logging import get_logger
from src.core.redis import check_kill_switch, set_kill_switch, get_redis
from src.models import (
    Influencer,
    Conversation,
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

router = APIRouter()
logger = get_logger(__name__)


# Dependency providers
async def get_gmail_adapter() -> GmailAdapter:
    adapter = GmailAdapter()
    await adapter.initialize()
    return adapter


async def get_slack_adapter() -> SlackAdapter:
    adapter = SlackAdapter()
    await adapter.initialize()
    return adapter


# Health check
@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check(
    session: AsyncSession = Depends(get_session),
) -> HealthResponse:
    """Check system health."""
    # Check database
    db_healthy = True
    try:
        await session.execute(select(Influencer).limit(1))
    except Exception:
        db_healthy = False

    # Check Redis
    redis_healthy = True
    try:
        redis = await get_redis()
        await redis.ping()
    except Exception:
        redis_healthy = False

    return HealthResponse(
        status="healthy" if db_healthy and redis_healthy else "degraded",
        version="0.1.0",
        database=db_healthy,
        redis=redis_healthy,
        gmail=False,  # Would need actual check
        slack=False,  # Would need actual check
    )


# Kill switch
@router.post("/kill-switch", response_model=KillSwitchResponse, tags=["System"])
async def toggle_kill_switch(
    request: KillSwitchRequest,
    slack: SlackAdapter = Depends(get_slack_adapter),
) -> KillSwitchResponse:
    """Toggle the global kill switch."""
    await set_kill_switch(request.enabled)

    if request.enabled:
        await slack.post_kill_switch_alert(request.user_id)

    logger.warning(
        "Kill switch toggled",
        enabled=request.enabled,
        user_id=request.user_id,
    )

    return KillSwitchResponse(
        enabled=request.enabled,
        changed_by=request.user_id,
        changed_at=datetime.utcnow(),
    )


@router.get("/kill-switch", tags=["System"])
async def get_kill_switch_status() -> dict[str, bool]:
    """Get kill switch status."""
    enabled = await check_kill_switch()
    return {"enabled": enabled}


# Influencer endpoints
@router.get("/influencers", response_model=list[InfluencerResponse], tags=["Influencers"])
async def list_influencers(
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[InfluencerResponse]:
    """List all influencers."""
    query = select(Influencer).offset(skip).limit(limit)
    if status:
        query = query.where(Influencer.status == status)

    result = await session.execute(query)
    influencers = result.scalars().all()
    return [InfluencerResponse.model_validate(i) for i in influencers]


@router.post("/influencers", response_model=InfluencerResponse, tags=["Influencers"])
async def create_influencer(
    data: InfluencerCreate,
    session: AsyncSession = Depends(get_session),
) -> InfluencerResponse:
    """Create a new influencer."""
    influencer = Influencer(**data.model_dump())
    session.add(influencer)
    await session.flush()
    await session.refresh(influencer)
    return InfluencerResponse.model_validate(influencer)


@router.get("/influencers/{influencer_id}", response_model=InfluencerResponse, tags=["Influencers"])
async def get_influencer(
    influencer_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> InfluencerResponse:
    """Get an influencer by ID."""
    result = await session.execute(
        select(Influencer).where(Influencer.id == influencer_id)
    )
    influencer = result.scalar_one_or_none()
    if not influencer:
        raise HTTPException(status_code=404, detail="Influencer not found")
    return InfluencerResponse.model_validate(influencer)


@router.patch("/influencers/{influencer_id}", response_model=InfluencerResponse, tags=["Influencers"])
async def update_influencer(
    influencer_id: UUID,
    data: InfluencerUpdate,
    session: AsyncSession = Depends(get_session),
) -> InfluencerResponse:
    """Update an influencer."""
    result = await session.execute(
        select(Influencer).where(Influencer.id == influencer_id)
    )
    influencer = result.scalar_one_or_none()
    if not influencer:
        raise HTTPException(status_code=404, detail="Influencer not found")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(influencer, field, value)

    await session.flush()
    await session.refresh(influencer)
    return InfluencerResponse.model_validate(influencer)


# Conversation endpoints
@router.get("/conversations", response_model=list[ConversationResponse], tags=["Conversations"])
async def list_conversations(
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    influencer_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ConversationResponse]:
    """List conversations."""
    query = select(Conversation).offset(skip).limit(limit)
    if status:
        query = query.where(Conversation.status == status)
    if influencer_id:
        query = query.where(Conversation.influencer_id == influencer_id)

    result = await session.execute(query)
    conversations = result.scalars().all()
    return [ConversationResponse.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse, tags=["Conversations"])
async def get_conversation(
    conversation_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ConversationResponse:
    """Get a conversation by ID."""
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationResponse.model_validate(conversation)


# Task endpoints
@router.get("/tasks", response_model=list[TaskResponse], tags=["Tasks"])
async def list_tasks(
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    conversation_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[TaskResponse]:
    """List tasks."""
    query = select(Task).offset(skip).limit(limit).order_by(Task.created_at.desc())
    if status:
        query = query.where(Task.status == status)
    if conversation_id:
        query = query.where(Task.conversation_id == conversation_id)

    result = await session.execute(query)
    tasks = result.scalars().all()
    return [TaskResponse.model_validate(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskResponse, tags=["Tasks"])
async def get_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Get a task by ID."""
    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse.model_validate(task)


@router.post("/tasks/{task_id}/approve", response_model=TaskResponse, tags=["Tasks"])
async def approve_task(
    task_id: UUID,
    approval: TaskApproval,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> TaskResponse:
    """Approve or reject a task."""
    # Check kill switch
    if await check_kill_switch():
        raise HTTPException(
            status_code=503,
            detail="Kill switch is active - all operations are paused",
        )

    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != TaskStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=400,
            detail=f"Task is not awaiting approval (current status: {task.status})",
        )

    if approval.approved:
        task.status = TaskStatus.APPROVED
        task.approval_user_id = approval.user_id
        task.approval_timestamp = datetime.utcnow()

        if approval.edited_content:
            task.draft_content = approval.edited_content

        # Schedule sending in background
        background_tasks.add_task(send_approved_email, str(task.id))
    else:
        task.status = TaskStatus.REJECTED
        task.rejection_reason = approval.reason
        task.approval_user_id = approval.user_id

    # Create audit log
    audit_log = AuditLog(
        task_id=task.id,
        action="task_approved" if approval.approved else "task_rejected",
        actor=approval.user_id,
        actor_type="user",
        timestamp=datetime.utcnow(),
        description=approval.reason,
    )
    session.add(audit_log)

    await session.flush()
    await session.refresh(task)
    return TaskResponse.model_validate(task)


async def send_approved_email(task_id: str) -> None:
    """Background task to send approved email."""
    # This would be implemented with actual Gmail sending
    logger.info("Sending approved email", task_id=task_id)


# Email processing endpoints
@router.post("/emails/triage", response_model=EmailTriageResponse, tags=["Emails"])
async def triage_email(
    data: EmailTriageRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> EmailTriageResponse:
    """Triage an incoming email."""
    # Check kill switch
    if await check_kill_switch():
        raise HTTPException(
            status_code=503,
            detail="Kill switch is active - all operations are paused",
        )

    triage_agent = TriageAgent()

    # Find or create influencer
    result = await session.execute(
        select(Influencer).where(Influencer.email == data.sender)
    )
    influencer = result.scalar_one_or_none()

    influencer_context = None
    if influencer:
        influencer_context = {
            "name": influencer.name,
            "status": influencer.status.value,
            "risk_level": influencer.risk_level.value,
        }

    # Triage the email
    triage_result = await triage_agent.analyze(
        email_body=data.body,
        email_subject=data.subject,
        sender=data.sender,
        influencer_context=influencer_context,
    )

    # Create or find conversation
    result = await session.execute(
        select(Conversation).where(Conversation.thread_id == data.thread_id)
    )
    conversation = result.scalar_one_or_none()

    if not conversation and influencer:
        conversation = Conversation(
            influencer_id=influencer.id,
            thread_id=data.thread_id,
            subject=data.subject,
            primary_intent=triage_result.intent.value,
            sentiment=triage_result.sentiment.value,
            priority=triage_result.priority.value,
        )
        session.add(conversation)
        await session.flush()

    # Create message
    if conversation:
        message = Message(
            conversation_id=conversation.id,
            direction=MessageDirection.INBOUND,
            content=data.body,
            timestamp=data.timestamp,
            message_id=data.message_id,
            subject=data.subject,
            sender=data.sender,
        )
        session.add(message)

        # Update conversation
        conversation.last_message_at = data.timestamp
        conversation.primary_intent = triage_result.intent.value

    # Create task if action needed
    task_id = None
    if not triage_result.requires_human and conversation:
        task = Task(
            conversation_id=conversation.id,
            type=TaskType.DRAFT_REPLY,
            status=TaskStatus.PENDING,
            confidence_score=triage_result.confidence,
            intent_detected=triage_result.intent.value,
        )
        session.add(task)
        await session.flush()
        task_id = task.id

        # Schedule draft generation
        background_tasks.add_task(generate_draft_for_task, str(task.id))

    await session.flush()

    return EmailTriageResponse(
        intent=triage_result.intent.value,
        confidence=triage_result.confidence,
        priority=triage_result.priority.value,
        sentiment=triage_result.sentiment.value,
        summary=triage_result.summary,
        key_points=triage_result.key_points,
        suggested_action=triage_result.suggested_action,
        requires_human=triage_result.requires_human,
        conversation_id=conversation.id if conversation else None,
        task_id=task_id,
    )


async def generate_draft_for_task(task_id: str) -> None:
    """Background task to generate draft."""
    logger.info("Generating draft for task", task_id=task_id)


@router.post("/emails/draft", response_model=DraftResponse, tags=["Emails"])
async def generate_draft(
    data: DraftRequest,
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Generate a draft reply for a conversation."""
    # Check kill switch
    if await check_kill_switch():
        raise HTTPException(
            status_code=503,
            detail="Kill switch is active - all operations are paused",
        )

    # Get conversation with messages
    result = await session.execute(
        select(Conversation).where(Conversation.id == data.conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

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
        raise HTTPException(status_code=400, detail="No messages in conversation")

    # Generate draft
    drafting_service = DraftingService()
    from src.agents.triage import Intent

    intent = Intent(conversation.primary_intent) if conversation.primary_intent else Intent.GENERAL_QUESTION

    draft_result = await drafting_service.generate_draft(
        intent=intent,
        original_email=latest_message.content,
        original_subject=latest_message.subject or conversation.subject or "",
        sender_name=influencer.name if influencer else "Influencer",
        additional_instructions=data.additional_instructions,
        influencer_context={
            "name": influencer.name,
            "status": influencer.status.value,
        } if influencer else None,
    )

    # Check guardrails
    guardrail_service = GuardrailService()
    guardrail_result = await guardrail_service.check_content(draft_result.body)

    # Create task
    task = Task(
        conversation_id=conversation.id,
        type=TaskType.DRAFT_REPLY,
        status=TaskStatus.AWAITING_APPROVAL,
        draft_subject=draft_result.subject,
        draft_content=draft_result.body,
        confidence_score=draft_result.confidence,
        intent_detected=intent.value,
        context_used={"sources": draft_result.sources_used},
    )
    session.add(task)
    await session.flush()

    return DraftResponse(
        task_id=task.id,
        subject=draft_result.subject,
        body=draft_result.body,
        confidence=draft_result.confidence,
        reasoning=draft_result.reasoning,
        guardrail_passed=guardrail_result.passed,
        guardrail_warnings=[v.value for v in guardrail_result.violations],
    )


# Slack webhook endpoints
@router.post("/slack/events", tags=["Slack"])
async def slack_events(request: Request) -> dict[str, Any]:
    """Handle Slack events."""
    body = await request.json()

    # URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # Handle events
    event = body.get("event", {})
    event_type = event.get("type")

    logger.info("Slack event received", event_type=event_type)

    return {"ok": True}


@router.post("/slack/interactions", tags=["Slack"])
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Handle Slack interactive components."""
    # Parse form data
    form_data = await request.form()
    import json
    payload = json.loads(form_data.get("payload", "{}"))

    action_type = payload.get("type")
    user = payload.get("user", {})
    actions = payload.get("actions", [])

    for action in actions:
        action_id = action.get("action_id")
        value = action.get("value")

        if action_id == "approve_draft":
            background_tasks.add_task(
                process_approval,
                task_id=value,
                user_id=user.get("id"),
                approved=True,
                channel=payload.get("channel", {}).get("id"),
                message_ts=payload.get("message", {}).get("ts"),
            )

        elif action_id == "reject_draft":
            background_tasks.add_task(
                process_approval,
                task_id=value,
                user_id=user.get("id"),
                approved=False,
                channel=payload.get("channel", {}).get("id"),
                message_ts=payload.get("message", {}).get("ts"),
            )

    return {"ok": True}


async def process_approval(
    task_id: str,
    user_id: str,
    approved: bool,
    channel: str,
    message_ts: str,
) -> None:
    """Process task approval from Slack."""
    logger.info(
        "Processing approval",
        task_id=task_id,
        user_id=user_id,
        approved=approved,
    )


# Sync endpoints
@router.post("/sync", response_model=SyncResponse, tags=["Sync"])
async def trigger_sync(
    data: SyncRequest,
    background_tasks: BackgroundTasks,
) -> SyncResponse:
    """Trigger a sync operation."""
    started_at = datetime.utcnow()

    if data.source == "notion":
        background_tasks.add_task(sync_notion_sops)
    elif data.source == "hubspot":
        background_tasks.add_task(sync_hubspot_contacts)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown source: {data.source}")

    return SyncResponse(
        source=data.source,
        records_synced=0,
        started_at=started_at,
        completed_at=None,
        status="in_progress",
    )


async def sync_notion_sops() -> None:
    """Sync SOPs from Notion."""
    logger.info("Syncing Notion SOPs")
    rag_service = RAGService()
    await rag_service.initialize()
    try:
        count = await rag_service.sync_sops_from_notion()
        logger.info("Notion sync complete", count=count)
    finally:
        await rag_service.close()


async def sync_hubspot_contacts() -> None:
    """Sync contacts from HubSpot."""
    logger.info("Syncing HubSpot contacts")


# Dashboard endpoints
@router.get("/dashboard/metrics", tags=["Dashboard"])
async def get_dashboard_metrics(
    hours: int = 24,
) -> dict[str, Any]:
    """Get dashboard metrics for the specified time period."""
    from src.services.metrics import MetricsService, TimeSavingsCalculator

    metrics_service = MetricsService()
    dashboard_metrics = await metrics_service.get_dashboard_metrics(hours=hours)

    # Calculate time saved
    time_saved = TimeSavingsCalculator.calculate_time_saved(
        emails_triaged=dashboard_metrics.total_emails_received,
        drafts_generated=dashboard_metrics.total_drafts_generated,
        emails_sent=dashboard_metrics.total_emails_sent,
    )

    return {
        "period_start": dashboard_metrics.period_start.isoformat(),
        "period_end": dashboard_metrics.period_end.isoformat(),
        "total_emails_received": dashboard_metrics.total_emails_received,
        "total_emails_sent": dashboard_metrics.total_emails_sent,
        "total_drafts_generated": dashboard_metrics.total_drafts_generated,
        "approval_rate": dashboard_metrics.approval_rate,
        "rejection_rate": dashboard_metrics.rejection_rate,
        "edit_rate": dashboard_metrics.edit_rate,
        "avg_response_time_seconds": dashboard_metrics.avg_response_time_seconds,
        "avg_confidence_score": dashboard_metrics.avg_confidence_score,
        "total_tokens_used": dashboard_metrics.total_tokens_used,
        "estimated_cost_usd": dashboard_metrics.estimated_cost_usd,
        "error_count": dashboard_metrics.error_count,
        "error_rate": dashboard_metrics.error_rate,
        "intent_distribution": dashboard_metrics.intent_distribution,
        "hourly_volume": dashboard_metrics.hourly_volume,
        "time_saved_minutes": time_saved["total_minutes"],
        "time_saved_hours": time_saved["total_hours"],
        "time_saved_breakdown": time_saved["breakdown"],
    }


@router.get("/dashboard/rag-stats", tags=["Dashboard"])
async def get_rag_stats() -> dict[str, Any]:
    """Get RAG service statistics."""
    rag_service = RAGService()
    await rag_service.initialize()
    try:
        return rag_service.get_stats()
    finally:
        await rag_service.close()


@router.get("/dashboard/workflows", tags=["Dashboard"])
async def get_workflow_stats() -> dict[str, Any]:
    """Get workflow statistics."""
    from src.services.workflow import get_workflow_engine, WorkflowState

    engine = get_workflow_engine()
    redis = await get_redis()

    # Count workflows by state
    workflows_by_state: dict[str, int] = {state.value: 0 for state in WorkflowState}
    active_count = 0

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="workflow:*", count=100)
        for key in keys:
            data = await redis.get(key)
            if data:
                import json
                state_data = json.loads(data)
                state = state_data.get("current_state", "unknown")
                if state in workflows_by_state:
                    workflows_by_state[state] += 1
                    if state not in ["closed", "failed"]:
                        active_count += 1
        if cursor == 0:
            break

    # Check for timeouts
    timed_out = await engine.check_timeouts()

    return {
        "active_workflows": active_count,
        "workflows_by_state": workflows_by_state,
        "timed_out_count": len(timed_out),
    }


@router.get("/dashboard/agent-mode", tags=["Dashboard"])
async def get_agent_mode() -> dict[str, Any]:
    """Get current agent mode configuration."""
    return {
        "mode": settings.agent_mode,
        "autopilot_confidence_threshold": settings.autopilot_confidence_threshold,
        "autopilot_allowed_intents": settings.autopilot_allowed_intents,
    }


@router.post("/dashboard/agent-mode", tags=["Dashboard"])
async def set_agent_mode(
    mode: str,
) -> dict[str, Any]:
    """Set agent mode (copilot or autopilot)."""
    if mode not in ["copilot", "autopilot"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Use 'copilot' or 'autopilot'")

    # Store in Redis for runtime configuration
    redis = await get_redis()
    await redis.set("agent_mode", mode)

    logger.info("Agent mode changed", mode=mode)

    return {
        "mode": mode,
        "message": f"Agent mode set to {mode}",
    }


@router.get("/dashboard/recent-activity", tags=["Dashboard"])
async def get_recent_activity(
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get recent activity (tasks, messages, etc.)."""
    # Get recent tasks
    result = await session.execute(
        select(Task)
        .order_by(Task.created_at.desc())
        .limit(limit)
    )
    tasks = result.scalars().all()

    activities = []
    for task in tasks:
        activities.append({
            "type": "task",
            "id": str(task.id),
            "action": task.type.value,
            "status": task.status.value,
            "confidence": task.confidence_score,
            "timestamp": task.created_at.isoformat(),
        })

    return activities


@router.get("/dashboard/conversation-summary", tags=["Dashboard"])
async def get_conversation_summary(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get summary of conversations by status."""
    from sqlalchemy import func

    # Count by status
    result = await session.execute(
        select(Conversation.status, func.count(Conversation.id))
        .group_by(Conversation.status)
    )
    status_counts = {row[0].value: row[1] for row in result.all()}

    # Count by intent
    result = await session.execute(
        select(Conversation.primary_intent, func.count(Conversation.id))
        .where(Conversation.primary_intent.isnot(None))
        .group_by(Conversation.primary_intent)
    )
    intent_counts = {row[0]: row[1] for row in result.all()}

    # Get conversations needing action
    result = await session.execute(
        select(func.count(Conversation.id))
        .where(Conversation.status == "needs_action")
    )
    needs_action = result.scalar() or 0

    return {
        "by_status": status_counts,
        "by_intent": intent_counts,
        "needs_action": needs_action,
    }


# =============================================================================
# Prospecting Endpoints
# =============================================================================

@router.post("/prospecting/campaigns", response_model=CampaignResponse, tags=["Prospecting"])
async def create_campaign(
    data: CampaignCreateRequest,
) -> CampaignResponse:
    """Create a new prospecting campaign."""
    from src.services.prospecting import get_prospecting_service, CampaignConfig

    service = await get_prospecting_service()

    config = CampaignConfig(
        name=data.name,
        description=data.description,
        target_status=data.target_status,
        target_tags=data.target_tags,
        max_prospects=data.max_prospects,
        daily_limit=data.daily_limit,
        subject_template=data.subject_template,
        auto_approve=data.auto_approve,
        confidence_threshold=data.confidence_threshold,
    )

    campaign_id = await service.create_campaign(config)

    return CampaignResponse(
        id=campaign_id,
        name=data.name,
        description=data.description,
        status="draft",
        target_status=data.target_status,
        max_prospects=data.max_prospects,
        daily_limit=data.daily_limit,
        auto_approve=data.auto_approve,
        confidence_threshold=data.confidence_threshold,
        prospects_loaded=0,
        prospects_processed=0,
        prospects_sent=0,
        created_at=config.created_at,
    )


@router.get("/prospecting/campaigns", response_model=list[CampaignResponse], tags=["Prospecting"])
async def list_campaigns() -> list[CampaignResponse]:
    """List all prospecting campaigns."""
    from src.services.prospecting import get_prospecting_service
    from datetime import datetime

    service = await get_prospecting_service()
    campaigns = await service.list_campaigns()

    return [
        CampaignResponse(
            id=c["id"],
            name=c["name"],
            description=c["description"],
            status=c.get("status", "draft"),
            target_status=c.get("target_status", "prospect"),
            max_prospects=c.get("max_prospects", 100),
            daily_limit=c.get("daily_limit", 20),
            auto_approve=c.get("auto_approve", False),
            confidence_threshold=c.get("confidence_threshold", 0.85),
            prospects_loaded=c.get("prospects_loaded", 0),
            prospects_processed=c.get("prospects_processed", 0),
            prospects_sent=c.get("prospects_sent", 0),
            created_at=datetime.fromisoformat(c["created_at"]) if c.get("created_at") else datetime.utcnow(),
        )
        for c in campaigns
    ]


@router.get("/prospecting/campaigns/{campaign_id}", response_model=CampaignResponse, tags=["Prospecting"])
async def get_campaign(campaign_id: str) -> CampaignResponse:
    """Get a specific campaign."""
    redis = await get_redis()
    import json
    from datetime import datetime

    data = await redis.get(f"campaign:{campaign_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    c = json.loads(data)

    return CampaignResponse(
        id=c["id"],
        name=c["name"],
        description=c["description"],
        status=c.get("status", "draft"),
        target_status=c.get("target_status", "prospect"),
        max_prospects=c.get("max_prospects", 100),
        daily_limit=c.get("daily_limit", 20),
        auto_approve=c.get("auto_approve", False),
        confidence_threshold=c.get("confidence_threshold", 0.85),
        prospects_loaded=c.get("prospects_loaded", 0),
        prospects_processed=c.get("prospects_processed", 0),
        prospects_sent=c.get("prospects_sent", 0),
        created_at=datetime.fromisoformat(c["created_at"]) if c.get("created_at") else datetime.utcnow(),
    )


@router.post("/prospecting/campaigns/{campaign_id}/load-prospects", tags=["Prospecting"])
async def load_campaign_prospects(
    campaign_id: str,
    lifecycle_stage: str = "lead",
) -> dict[str, Any]:
    """Load prospects from HubSpot for a campaign."""
    from src.services.prospecting import get_prospecting_service

    service = await get_prospecting_service()

    try:
        contacts = await service.load_prospects_for_campaign(
            campaign_id=campaign_id,
            lifecycle_stage=lifecycle_stage,
        )
        return {
            "campaign_id": campaign_id,
            "prospects_loaded": len(contacts),
            "status": "success",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/prospecting/campaigns/{campaign_id}/start", tags=["Prospecting"])
async def start_campaign(campaign_id: str) -> dict[str, str]:
    """Start a campaign."""
    from src.services.prospecting import get_prospecting_service

    service = await get_prospecting_service()

    try:
        await service.start_campaign(campaign_id)
        return {"status": "started", "campaign_id": campaign_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/prospecting/campaigns/{campaign_id}/pause", tags=["Prospecting"])
async def pause_campaign(campaign_id: str) -> dict[str, str]:
    """Pause a campaign."""
    from src.services.prospecting import get_prospecting_service

    service = await get_prospecting_service()

    try:
        await service.pause_campaign(campaign_id)
        return {"status": "paused", "campaign_id": campaign_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/prospecting/campaigns/{campaign_id}/progress",
    response_model=CampaignProgressResponse,
    tags=["Prospecting"],
)
async def get_campaign_progress(campaign_id: str) -> CampaignProgressResponse:
    """Get campaign progress."""
    from src.services.prospecting import get_prospecting_service

    service = await get_prospecting_service()

    try:
        progress = await service.get_campaign_progress(campaign_id)
        return CampaignProgressResponse(
            campaign_id=progress.campaign_id,
            total_prospects=progress.total_prospects,
            processed=progress.processed,
            sent=progress.sent,
            pending_approval=progress.pending_approval,
            failed=progress.failed,
            skipped=progress.skipped,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/prospecting/campaigns/{campaign_id}/process-batch",
    response_model=CampaignBatchResponse,
    tags=["Prospecting"],
)
async def process_campaign_batch(
    campaign_id: str,
    batch_size: int = 10,
    background_tasks: BackgroundTasks = None,
) -> CampaignBatchResponse:
    """Process a batch of prospects for a campaign."""
    from src.services.prospecting import get_prospecting_service

    # Check kill switch
    if await check_kill_switch():
        raise HTTPException(
            status_code=503,
            detail="Kill switch is active - all operations are paused",
        )

    service = await get_prospecting_service()

    try:
        results = await service.process_campaign_batch(campaign_id, batch_size)
        return CampaignBatchResponse(
            campaign_id=campaign_id,
            processed=len(results),
            results=[
                ProspectResultResponse(
                    hubspot_id=r.hubspot_id,
                    email=r.email,
                    status=r.status.value,
                    draft_subject=r.draft_subject,
                    confidence=r.confidence,
                    task_id=r.task_id,
                    error=r.error,
                )
                for r in results
            ],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# Autopilot Endpoints
# =============================================================================

@router.get("/autopilot/stats", response_model=AutopilotStatsResponse, tags=["Autopilot"])
async def get_autopilot_stats() -> AutopilotStatsResponse:
    """Get autopilot decision statistics."""
    from src.services.autopilot import get_autopilot_engine

    engine = get_autopilot_engine()
    recent = await engine.get_recent_decisions(limit=100)

    # Calculate stats
    total = len(recent)
    auto_sent = sum(1 for d in recent if d.get("decision") == "auto_send")
    human_review = sum(1 for d in recent if d.get("decision") == "human_review")
    escalated = sum(1 for d in recent if d.get("decision") == "escalate")
    blocked = sum(1 for d in recent if d.get("decision") == "block")

    confidences = [
        d.get("confidence", {}).get("overall", 0)
        for d in recent
        if d.get("confidence")
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    return AutopilotStatsResponse(
        total_decisions=total,
        auto_sent=auto_sent,
        human_review=human_review,
        escalated=escalated,
        blocked=blocked,
        avg_confidence=avg_confidence,
        recent_decisions=recent[:20],  # Return last 20 for display
    )


@router.get("/autopilot/decisions", tags=["Autopilot"])
async def get_autopilot_decisions(
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get recent autopilot decisions."""
    from src.services.autopilot import get_autopilot_engine

    engine = get_autopilot_engine()
    return await engine.get_recent_decisions(limit=limit)


@router.get("/autopilot/decision/{task_id}", tags=["Autopilot"])
async def get_autopilot_decision(task_id: UUID) -> dict[str, Any]:
    """Get autopilot decision for a specific task."""
    redis = await get_redis()
    import json

    data = await redis.get(f"autopilot:decision:{task_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Decision not found")

    return json.loads(data)


@router.post("/autopilot/confidence-threshold", tags=["Autopilot"])
async def set_confidence_threshold(
    threshold: float,
) -> dict[str, Any]:
    """Set autopilot confidence threshold (runtime override)."""
    if threshold < 0.5 or threshold > 1.0:
        raise HTTPException(
            status_code=400,
            detail="Threshold must be between 0.5 and 1.0",
        )

    redis = await get_redis()
    await redis.set("autopilot:confidence_threshold", str(threshold))

    logger.info("Autopilot confidence threshold changed", threshold=threshold)

    return {
        "threshold": threshold,
        "message": f"Confidence threshold set to {threshold}",
    }


@router.get("/autopilot/allowed-intents", tags=["Autopilot"])
async def get_allowed_intents() -> dict[str, Any]:
    """Get list of intents allowed for autopilot."""
    return {
        "allowed_intents": settings.autopilot_allowed_intents,
        "message": "Intents that can be auto-sent in autopilot mode",
    }


@router.post("/autopilot/allowed-intents", tags=["Autopilot"])
async def set_allowed_intents(
    intents: list[str],
) -> dict[str, Any]:
    """Set allowed intents for autopilot (runtime override)."""
    redis = await get_redis()
    import json

    await redis.set("autopilot:allowed_intents", json.dumps(intents))

    logger.info("Autopilot allowed intents changed", intents=intents)

    return {
        "allowed_intents": intents,
        "message": f"Allowed intents updated",
    }


# =============================================================================
# Audit Endpoints
# =============================================================================

@router.get("/audit/events", tags=["Audit"])
async def get_audit_events(
    limit: int = 100,
    category: str | None = None,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    """Get recent audit events."""
    from src.services.audit import get_audit_service, AuditCategory, AuditSeverity

    service = get_audit_service()

    cat = AuditCategory(category) if category else None
    sev = AuditSeverity(severity) if severity else None

    return await service.get_recent_events(limit=limit, category=cat, severity=sev)


@router.get("/audit/critical", tags=["Audit"])
async def get_critical_events(
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get critical audit events (errors and critical)."""
    from src.services.audit import get_audit_service, AuditSeverity

    service = get_audit_service()
    return await service.get_recent_events(limit=limit, severity=AuditSeverity.CRITICAL)


@router.get("/audit/llm-usage", tags=["Audit"])
async def get_llm_usage(
    days: int = 7,
) -> dict[str, Any]:
    """Get LLM usage statistics."""
    from src.services.audit import get_audit_service

    service = get_audit_service()
    return await service.get_llm_usage_stats(days=days)


@router.get("/audit/compliance-report", tags=["Audit"])
async def get_compliance_report(
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Generate compliance report for date range."""
    from src.services.audit import get_audit_service
    from datetime import datetime

    service = get_audit_service()

    try:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
        )

    return await service.get_compliance_report(start, end)


@router.get("/audit/task/{task_id}", tags=["Audit"])
async def get_task_audit_trail(
    task_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get complete audit trail for a specific task."""
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.task_id == task_id)
        .order_by(AuditLog.timestamp.asc())
    )
    logs = result.scalars().all()

    return [
        {
            "id": str(log.id),
            "task_id": str(log.task_id),
            "action": log.action,
            "actor": log.actor,
            "actor_type": log.actor_type,
            "timestamp": log.timestamp.isoformat(),
            "description": log.description,
            "metadata": log.metadata,
        }
        for log in logs
    ]


@router.get("/audit/categories", tags=["Audit"])
async def list_audit_categories() -> dict[str, list[str]]:
    """List available audit categories and severities."""
    from src.services.audit import AuditCategory, AuditSeverity

    return {
        "categories": [c.value for c in AuditCategory],
        "severities": [s.value for s in AuditSeverity],
    }
