from dataclasses import dataclass
from typing import Any, Callable, Awaitable

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError
from tenacity import retry, stop_after_attempt, wait_exponential

from src.adapters.base import BaseAdapter
from src.core.config import settings


@dataclass
class SlackMessage:
    """Represents a Slack message."""

    channel: str
    ts: str
    text: str
    user: str | None
    thread_ts: str | None


@dataclass
class ApprovalRequest:
    """Data for an approval request in Slack."""

    task_id: str
    conversation_id: str
    influencer_name: str
    influencer_email: str
    intent: str
    draft_subject: str
    draft_content: str
    context_summary: str
    confidence_score: float


class SlackAdapter(BaseAdapter):
    """Adapter for Slack API operations."""

    def __init__(self) -> None:
        super().__init__()
        self._client: AsyncWebClient | None = None
        self._action_handlers: dict[str, Callable[..., Awaitable[Any]]] = {}

    async def initialize(self) -> None:
        """Initialize Slack client."""
        token = settings.slack_bot_token.get_secret_value()
        if not token:
            raise ValueError("Slack bot token not configured")

        self._client = AsyncWebClient(token=token)

        # Verify connection
        try:
            auth_response = await self._client.auth_test()
            self.logger.info(
                "Slack adapter initialized",
                bot_user=auth_response.get("user"),
                team=auth_response.get("team"),
            )
            self._initialized = True
        except SlackApiError as e:
            self.logger.error("Failed to initialize Slack adapter", error=str(e))
            raise

    async def health_check(self) -> bool:
        """Check Slack connection."""
        if not self._client:
            return False
        try:
            await self._client.auth_test()
            return True
        except SlackApiError:
            return False

    async def close(self) -> None:
        """Close Slack adapter."""
        self._client = None
        self._initialized = False
        self.logger.info("Slack adapter closed")

    def register_action_handler(
        self, action_id: str, handler: Callable[..., Awaitable[Any]]
    ) -> None:
        """Register a handler for Slack interactive actions."""
        self._action_handlers[action_id] = handler

    async def handle_action(self, action_id: str, payload: dict[str, Any]) -> Any:
        """Handle a Slack interactive action."""
        handler = self._action_handlers.get(action_id)
        if handler:
            return await handler(payload)
        self.logger.warning("No handler for action", action_id=action_id)
        return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
        thread_ts: str | None = None,
    ) -> SlackMessage:
        """Send a message to a Slack channel."""
        if not self._client:
            raise RuntimeError("Slack adapter not initialized")

        try:
            response = await self._client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks,
                thread_ts=thread_ts,
            )
            return SlackMessage(
                channel=response["channel"],
                ts=response["ts"],
                text=text,
                user=None,
                thread_ts=thread_ts,
            )
        except SlackApiError as e:
            self.logger.error("Failed to send message", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Update an existing Slack message."""
        if not self._client:
            raise RuntimeError("Slack adapter not initialized")

        try:
            await self._client.chat_update(
                channel=channel,
                ts=ts,
                text=text,
                blocks=blocks,
            )
        except SlackApiError as e:
            self.logger.error("Failed to update message", error=str(e))
            raise

    async def post_approval_request(
        self, request: ApprovalRequest, channel: str | None = None
    ) -> SlackMessage:
        """Post an approval request with interactive buttons."""
        target_channel = channel or settings.slack_channel_id

        blocks = self._build_approval_blocks(request)

        return await self.send_message(
            channel=target_channel,
            text=f"New draft for approval: {request.draft_subject}",
            blocks=blocks,
        )

    def _build_approval_blocks(self, request: ApprovalRequest) -> list[dict[str, Any]]:
        """Build Block Kit blocks for approval request."""
        confidence_emoji = "🟢" if request.confidence_score >= 0.8 else "🟡" if request.confidence_score >= 0.5 else "🔴"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📧 Draft Ready for Review",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Influencer:*\n{request.influencer_name}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Email:*\n{request.influencer_email}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Intent:*\n{request.intent}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Confidence:*\n{confidence_emoji} {request.confidence_score:.0%}",
                    },
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Subject:* {request.draft_subject}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Draft:*\n```{request.draft_content[:2000]}{'...' if len(request.draft_content) > 2000 else ''}```",
                },
            },
        ]

        if request.context_summary:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"📋 *Context:* {request.context_summary}",
                    }
                ],
            })

        blocks.extend([
            {"type": "divider"},
            {
                "type": "actions",
                "block_id": f"approval_actions_{request.task_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "✅ Approve & Send",
                            "emoji": True,
                        },
                        "style": "primary",
                        "action_id": "approve_draft",
                        "value": request.task_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "✏️ Edit Draft",
                            "emoji": True,
                        },
                        "action_id": "edit_draft",
                        "value": request.task_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "❌ Reject",
                            "emoji": True,
                        },
                        "style": "danger",
                        "action_id": "reject_draft",
                        "value": request.task_id,
                    },
                ],
            },
        ])

        return blocks

    async def post_approval_result(
        self,
        channel: str,
        ts: str,
        approved: bool,
        approver: str,
        task_id: str,
    ) -> None:
        """Update the approval message with the result."""
        status_text = "✅ Approved and Sent" if approved else "❌ Rejected"

        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{status_text} by <@{approver}>",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Task ID: `{task_id}`",
                    }
                ],
            },
        ]

        await self.update_message(
            channel=channel,
            ts=ts,
            text=status_text,
            blocks=blocks,
        )

    async def post_new_email_alert(
        self,
        influencer_name: str,
        influencer_email: str,
        subject: str,
        preview: str,
        priority: str,
        channel: str | None = None,
    ) -> SlackMessage:
        """Post an alert about a new incoming email."""
        target_channel = channel or settings.slack_channel_id

        priority_emoji = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🟢"}.get(
            priority.lower(), "⚪"
        )

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📬 New Email Received",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*From:*\n{influencer_name} <{influencer_email}>",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Priority:*\n{priority_emoji} {priority.capitalize()}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Subject:* {subject}\n\n>{preview[:500]}{'...' if len(preview) > 500 else ''}",
                },
            },
        ]

        return await self.send_message(
            channel=target_channel,
            text=f"New email from {influencer_name}: {subject}",
            blocks=blocks,
        )

    async def post_kill_switch_alert(
        self, activated_by: str, channel: str | None = None
    ) -> SlackMessage:
        """Post an alert that the kill switch has been activated."""
        target_channel = channel or settings.slack_channel_id

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🛑 KILL SWITCH ACTIVATED",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"All automated operations have been stopped by <@{activated_by}>.\n\nTo resume operations, use `/ops-resume`.",
                },
            },
        ]

        return await self.send_message(
            channel=target_channel,
            text="🛑 Kill switch activated - all operations stopped",
            blocks=blocks,
        )
