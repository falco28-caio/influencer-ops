from __future__ import annotations

"""
Workflow Engine for managing conversation state machines.

This implements a state machine for email conversations with the following states:
- NEW: New inbound email received
- TRIAGING: Being classified by triage agent
- DRAFTING: Draft being generated
- REVIEWING: Awaiting human review
- APPROVED: Draft approved, ready to send
- SENDING: In the process of sending
- SENT: Email successfully sent
- WAITING_REPLY: Waiting for influencer response
- CLOSED: Conversation closed
- FAILED: Error state

Transitions are triggered by events and can have guards (conditions).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Awaitable
from uuid import UUID

from src.core.logging import get_logger
from src.core.redis import get_redis


class WorkflowState(str, Enum):
    """Conversation workflow states."""

    NEW = "new"
    TRIAGING = "triaging"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    EDITING = "editing"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    WAITING_REPLY = "waiting_reply"
    FOLLOW_UP_NEEDED = "follow_up_needed"
    CLOSED = "closed"
    FAILED = "failed"
    ESCALATED = "escalated"


class WorkflowEvent(str, Enum):
    """Events that trigger state transitions."""

    EMAIL_RECEIVED = "email_received"
    TRIAGE_COMPLETE = "triage_complete"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_REJECTED = "draft_rejected"
    DRAFT_EDITED = "draft_edited"
    SEND_STARTED = "send_started"
    SEND_COMPLETE = "send_complete"
    SEND_FAILED = "send_failed"
    REPLY_RECEIVED = "reply_received"
    FOLLOW_UP_TRIGGERED = "follow_up_triggered"
    CONVERSATION_CLOSED = "conversation_closed"
    ERROR_OCCURRED = "error_occurred"
    ESCALATE = "escalate"
    RETRY = "retry"


@dataclass
class Transition:
    """A state transition definition."""

    from_state: WorkflowState
    to_state: WorkflowState
    event: WorkflowEvent
    guard: Callable[[dict[str, Any]], bool] | None = None
    action: Callable[[dict[str, Any]], Awaitable[None]] | None = None


@dataclass
class WorkflowContext:
    """Context data for a workflow instance."""

    conversation_id: UUID
    current_state: WorkflowState
    task_id: UUID | None = None
    influencer_id: UUID | None = None
    intent: str | None = None
    confidence: float = 0.0
    retry_count: int = 0
    last_transition: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)


class WorkflowEngine:
    """
    State machine engine for managing conversation workflows.

    Supports:
    - State transitions based on events
    - Guards (conditions) for transitions
    - Actions triggered on transitions
    - State history tracking
    - Timeout handling
    """

    # State timeout configurations (in minutes)
    STATE_TIMEOUTS = {
        WorkflowState.TRIAGING: 5,
        WorkflowState.DRAFTING: 10,
        WorkflowState.REVIEWING: 1440,  # 24 hours
        WorkflowState.SENDING: 5,
        WorkflowState.WAITING_REPLY: 10080,  # 7 days
    }

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._transitions: list[Transition] = []
        self._state_enter_hooks: dict[WorkflowState, list[Callable]] = {}
        self._state_exit_hooks: dict[WorkflowState, list[Callable]] = {}
        self._setup_transitions()

    def _setup_transitions(self) -> None:
        """Define all valid state transitions."""
        transitions = [
            # Initial flow
            Transition(
                from_state=WorkflowState.NEW,
                to_state=WorkflowState.TRIAGING,
                event=WorkflowEvent.EMAIL_RECEIVED,
            ),
            Transition(
                from_state=WorkflowState.TRIAGING,
                to_state=WorkflowState.DRAFTING,
                event=WorkflowEvent.TRIAGE_COMPLETE,
                guard=lambda ctx: ctx.get("intent") not in ["spam", "out_of_scope"],
            ),
            Transition(
                from_state=WorkflowState.TRIAGING,
                to_state=WorkflowState.CLOSED,
                event=WorkflowEvent.TRIAGE_COMPLETE,
                guard=lambda ctx: ctx.get("intent") in ["spam", "out_of_scope"],
            ),
            # Drafting flow
            Transition(
                from_state=WorkflowState.DRAFTING,
                to_state=WorkflowState.REVIEWING,
                event=WorkflowEvent.DRAFT_GENERATED,
            ),
            Transition(
                from_state=WorkflowState.DRAFTING,
                to_state=WorkflowState.FAILED,
                event=WorkflowEvent.ERROR_OCCURRED,
            ),
            # Review flow
            Transition(
                from_state=WorkflowState.REVIEWING,
                to_state=WorkflowState.APPROVED,
                event=WorkflowEvent.DRAFT_APPROVED,
            ),
            Transition(
                from_state=WorkflowState.REVIEWING,
                to_state=WorkflowState.EDITING,
                event=WorkflowEvent.DRAFT_REJECTED,
            ),
            Transition(
                from_state=WorkflowState.REVIEWING,
                to_state=WorkflowState.ESCALATED,
                event=WorkflowEvent.ESCALATE,
            ),
            # Editing flow
            Transition(
                from_state=WorkflowState.EDITING,
                to_state=WorkflowState.REVIEWING,
                event=WorkflowEvent.DRAFT_EDITED,
            ),
            Transition(
                from_state=WorkflowState.EDITING,
                to_state=WorkflowState.DRAFTING,
                event=WorkflowEvent.RETRY,
            ),
            # Sending flow
            Transition(
                from_state=WorkflowState.APPROVED,
                to_state=WorkflowState.SENDING,
                event=WorkflowEvent.SEND_STARTED,
            ),
            Transition(
                from_state=WorkflowState.SENDING,
                to_state=WorkflowState.SENT,
                event=WorkflowEvent.SEND_COMPLETE,
            ),
            Transition(
                from_state=WorkflowState.SENDING,
                to_state=WorkflowState.FAILED,
                event=WorkflowEvent.SEND_FAILED,
            ),
            # Post-send flow
            Transition(
                from_state=WorkflowState.SENT,
                to_state=WorkflowState.WAITING_REPLY,
                event=WorkflowEvent.SEND_COMPLETE,
            ),
            Transition(
                from_state=WorkflowState.WAITING_REPLY,
                to_state=WorkflowState.NEW,
                event=WorkflowEvent.REPLY_RECEIVED,
            ),
            Transition(
                from_state=WorkflowState.WAITING_REPLY,
                to_state=WorkflowState.FOLLOW_UP_NEEDED,
                event=WorkflowEvent.FOLLOW_UP_TRIGGERED,
            ),
            Transition(
                from_state=WorkflowState.WAITING_REPLY,
                to_state=WorkflowState.CLOSED,
                event=WorkflowEvent.CONVERSATION_CLOSED,
            ),
            # Follow-up flow
            Transition(
                from_state=WorkflowState.FOLLOW_UP_NEEDED,
                to_state=WorkflowState.DRAFTING,
                event=WorkflowEvent.EMAIL_RECEIVED,
            ),
            # Recovery flows
            Transition(
                from_state=WorkflowState.FAILED,
                to_state=WorkflowState.DRAFTING,
                event=WorkflowEvent.RETRY,
                guard=lambda ctx: ctx.get("retry_count", 0) < 3,
            ),
            Transition(
                from_state=WorkflowState.FAILED,
                to_state=WorkflowState.ESCALATED,
                event=WorkflowEvent.ESCALATE,
            ),
            Transition(
                from_state=WorkflowState.ESCALATED,
                to_state=WorkflowState.REVIEWING,
                event=WorkflowEvent.DRAFT_EDITED,
            ),
            # Close from any state
            Transition(
                from_state=WorkflowState.ESCALATED,
                to_state=WorkflowState.CLOSED,
                event=WorkflowEvent.CONVERSATION_CLOSED,
            ),
        ]

        self._transitions = transitions

    def get_valid_transitions(
        self, current_state: WorkflowState
    ) -> list[WorkflowEvent]:
        """Get list of valid events from current state."""
        return [
            t.event
            for t in self._transitions
            if t.from_state == current_state
        ]

    def can_transition(
        self,
        current_state: WorkflowState,
        event: WorkflowEvent,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a transition is valid."""
        context = context or {}
        for transition in self._transitions:
            if (
                transition.from_state == current_state
                and transition.event == event
            ):
                if transition.guard is None:
                    return True
                return transition.guard(context)
        return False

    def get_next_state(
        self,
        current_state: WorkflowState,
        event: WorkflowEvent,
        context: dict[str, Any] | None = None,
    ) -> WorkflowState | None:
        """Get the next state for a given event."""
        context = context or {}
        for transition in self._transitions:
            if (
                transition.from_state == current_state
                and transition.event == event
            ):
                if transition.guard is None or transition.guard(context):
                    return transition.to_state
        return None

    async def process_event(
        self,
        workflow_context: WorkflowContext,
        event: WorkflowEvent,
        event_data: dict[str, Any] | None = None,
    ) -> WorkflowContext:
        """
        Process an event and transition the workflow state.

        Returns the updated workflow context.
        """
        event_data = event_data or {}
        current_state = workflow_context.current_state

        # Merge event data into context metadata
        context_dict = {
            **workflow_context.metadata,
            **event_data,
            "intent": workflow_context.intent,
            "confidence": workflow_context.confidence,
            "retry_count": workflow_context.retry_count,
        }

        # Find valid transition
        next_state = self.get_next_state(current_state, event, context_dict)

        if next_state is None:
            self.logger.warning(
                "Invalid transition",
                current_state=current_state.value,
                event=event.value,
                conversation_id=str(workflow_context.conversation_id),
            )
            return workflow_context

        # Execute exit hooks
        if current_state in self._state_exit_hooks:
            for hook in self._state_exit_hooks[current_state]:
                await hook(workflow_context, event)

        # Update context
        workflow_context.current_state = next_state
        workflow_context.last_transition = datetime.utcnow()
        workflow_context.history.append({
            "from_state": current_state.value,
            "to_state": next_state.value,
            "event": event.value,
            "timestamp": datetime.utcnow().isoformat(),
            "data": event_data,
        })

        # Update retry count on retry event
        if event == WorkflowEvent.RETRY:
            workflow_context.retry_count += 1

        # Execute enter hooks
        if next_state in self._state_enter_hooks:
            for hook in self._state_enter_hooks[next_state]:
                await hook(workflow_context, event)

        self.logger.info(
            "State transition",
            conversation_id=str(workflow_context.conversation_id),
            from_state=current_state.value,
            to_state=next_state.value,
            event=event.value,
        )

        # Persist state to Redis
        await self._persist_state(workflow_context)

        return workflow_context

    def on_state_enter(
        self, state: WorkflowState
    ) -> Callable[[Callable], Callable]:
        """Decorator to register a state enter hook."""

        def decorator(func: Callable) -> Callable:
            if state not in self._state_enter_hooks:
                self._state_enter_hooks[state] = []
            self._state_enter_hooks[state].append(func)
            return func

        return decorator

    def on_state_exit(
        self, state: WorkflowState
    ) -> Callable[[Callable], Callable]:
        """Decorator to register a state exit hook."""

        def decorator(func: Callable) -> Callable:
            if state not in self._state_exit_hooks:
                self._state_exit_hooks[state] = []
            self._state_exit_hooks[state].append(func)
            return func

        return decorator

    async def _persist_state(self, context: WorkflowContext) -> None:
        """Persist workflow state to Redis."""
        redis = await get_redis()
        key = f"workflow:{context.conversation_id}"

        state_data = {
            "current_state": context.current_state.value,
            "task_id": str(context.task_id) if context.task_id else None,
            "influencer_id": str(context.influencer_id) if context.influencer_id else None,
            "intent": context.intent,
            "confidence": context.confidence,
            "retry_count": context.retry_count,
            "last_transition": context.last_transition.isoformat() if context.last_transition else None,
            "metadata": context.metadata,
        }

        import json
        await redis.set(key, json.dumps(state_data), ex=86400 * 30)  # 30 days TTL

    async def load_state(self, conversation_id: UUID) -> WorkflowContext | None:
        """Load workflow state from Redis."""
        redis = await get_redis()
        key = f"workflow:{conversation_id}"

        data = await redis.get(key)
        if not data:
            return None

        import json
        state_data = json.loads(data)

        return WorkflowContext(
            conversation_id=conversation_id,
            current_state=WorkflowState(state_data["current_state"]),
            task_id=UUID(state_data["task_id"]) if state_data.get("task_id") else None,
            influencer_id=UUID(state_data["influencer_id"]) if state_data.get("influencer_id") else None,
            intent=state_data.get("intent"),
            confidence=state_data.get("confidence", 0.0),
            retry_count=state_data.get("retry_count", 0),
            last_transition=datetime.fromisoformat(state_data["last_transition"]) if state_data.get("last_transition") else None,
            metadata=state_data.get("metadata", {}),
        )

    async def check_timeouts(self) -> list[WorkflowContext]:
        """Check for workflows that have timed out in their current state."""
        redis = await get_redis()
        timed_out = []

        # Scan for all workflow keys
        cursor = 0
        while True:
            cursor, keys = await redis.scan(cursor, match="workflow:*", count=100)

            for key in keys:
                context = await self.load_state(UUID(key.split(":")[1]))
                if context and self._is_timed_out(context):
                    timed_out.append(context)

            if cursor == 0:
                break

        return timed_out

    def _is_timed_out(self, context: WorkflowContext) -> bool:
        """Check if a workflow has timed out."""
        timeout_minutes = self.STATE_TIMEOUTS.get(context.current_state)
        if not timeout_minutes or not context.last_transition:
            return False

        timeout_delta = timedelta(minutes=timeout_minutes)
        return datetime.utcnow() - context.last_transition > timeout_delta

    def create_context(
        self,
        conversation_id: UUID,
        influencer_id: UUID | None = None,
        initial_state: WorkflowState = WorkflowState.NEW,
    ) -> WorkflowContext:
        """Create a new workflow context."""
        return WorkflowContext(
            conversation_id=conversation_id,
            current_state=initial_state,
            influencer_id=influencer_id,
            last_transition=datetime.utcnow(),
        )


# Global workflow engine instance
_workflow_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    """Get the global workflow engine instance."""
    global _workflow_engine
    if _workflow_engine is None:
        _workflow_engine = WorkflowEngine()
    return _workflow_engine
