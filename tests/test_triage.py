import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.triage import TriageAgent, Intent, Priority, Sentiment


class TestTriageAgent:
    """Tests for the TriageAgent."""

    @pytest.fixture
    def triage_agent(self):
        """Create a TriageAgent instance."""
        with patch("src.agents.triage.anthropic.Anthropic"):
            return TriageAgent()

    def test_quick_classify_spam(self, triage_agent):
        """Test quick classification of spam emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("Please unsubscribe me from this list")
        )
        assert result[0] == Intent.SPAM

    def test_quick_classify_scheduling(self, triage_agent):
        """Test quick classification of scheduling emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("Can we schedule a call for next week?")
        )
        assert result[0] == Intent.SCHEDULE_MEETING

    def test_quick_classify_payment(self, triage_agent):
        """Test quick classification of payment emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("When will I receive the payment?")
        )
        assert result[0] == Intent.PAYMENT_INFO

    def test_quick_classify_rate_negotiation(self, triage_agent):
        """Test quick classification of rate negotiation emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("What are your rates for a sponsored post?")
        )
        assert result[0] == Intent.RATE_NEGOTIATION

    def test_quick_classify_collab(self, triage_agent):
        """Test quick classification of collaboration emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("I'd love to partner with your brand on a campaign")
        )
        assert result[0] == Intent.COLLAB_INQUIRY

    def test_quick_classify_general(self, triage_agent):
        """Test quick classification of general emails."""
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            triage_agent.quick_classify("Hello, hope you're doing well!")
        )
        assert result[0] == Intent.GENERAL_QUESTION


class TestGuardrailService:
    """Tests for the GuardrailService."""

    @pytest.fixture
    def guardrail_service(self):
        """Create a GuardrailService instance."""
        with patch("src.services.guardrail.anthropic.Anthropic"):
            from src.services.guardrail import GuardrailService
            return GuardrailService()

    def test_pii_detection_credit_card(self, guardrail_service):
        """Test detection of credit card numbers."""
        result = guardrail_service._check_pii("My card number is 4111-1111-1111-1111")
        assert result["found"] is True
        assert "credit_card" in result["types"]

    def test_pii_detection_phone(self, guardrail_service):
        """Test detection of phone numbers."""
        result = guardrail_service._check_pii("Call me at (555) 123-4567")
        assert result["found"] is True
        assert "phone" in result["types"]

    def test_pii_masking(self, guardrail_service):
        """Test PII masking."""
        content = "My card is 4111-1111-1111-1111 and phone is (555) 123-4567"
        masked = guardrail_service._mask_pii(content)
        assert "4111-1111-1111-1111" not in masked
        assert "(555) 123-4567" not in masked

    def test_financial_promise_detection(self, guardrail_service):
        """Test detection of financial promises."""
        result = guardrail_service._check_financial_promises(
            "We will pay you $5000 for this campaign"
        )
        assert result["found"] is True

    def test_no_financial_promise(self, guardrail_service):
        """Test that normal content passes."""
        result = guardrail_service._check_financial_promises(
            "Thank you for your interest in working with us"
        )
        assert result["found"] is False
