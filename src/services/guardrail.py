from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import anthropic

from src.core.config import settings
from src.core.logging import get_logger


class GuardrailViolation(str, Enum):
    """Types of guardrail violations."""

    PII_DETECTED = "pii_detected"
    FINANCIAL_PROMISE = "financial_promise"
    INAPPROPRIATE_TONE = "inappropriate_tone"
    POLICY_VIOLATION = "policy_violation"
    LENGTH_EXCEEDED = "length_exceeded"
    FORBIDDEN_TERM = "forbidden_term"
    PR_RISK = "pr_risk"
    HALLUCINATION_RISK = "hallucination_risk"
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK_ATTEMPT = "jailbreak_attempt"
    MALICIOUS_CONTENT = "malicious_content"


@dataclass
class GuardrailResult:
    """Result of guardrail check."""

    passed: bool
    violations: list[GuardrailViolation]
    details: dict[str, Any]
    masked_content: str | None = None
    suggestions: list[str] = field(default_factory=list)
    risk_score: float = 0.0


class GuardrailService:
    """Service for content validation and safety checks."""

    # PII patterns
    PII_PATTERNS = {
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "ssn": r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
        "phone": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "bank_account": r"\b\d{8,17}\b",
        "routing_number": r"\b\d{9}\b",
    }

    # Financial commitment patterns
    FINANCIAL_PATTERNS = [
        r"(?:will|shall|going to|we'll|I'll)\s+(?:pay|send|transfer|wire)\s+(?:\w+\s+)*\$?\d+",
        r"\$\d+(?:,\d{3})*(?:\.\d{2})?\s+(?:guaranteed|confirmed|promised)",
        r"(?:guarantee|promise|commit)\s+(?:to pay|payment of)\s+\$?\d+",
    ]

    # Prompt injection patterns - detect attempts to manipulate the AI
    PROMPT_INJECTION_PATTERNS = [
        # Direct instruction injection
        r"(?i)ignore\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?|rules?)",
        r"(?i)disregard\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?)",
        r"(?i)forget\s+(?:all\s+)?(?:your|the)\s+(?:instructions?|rules?|guidelines?)",
        r"(?i)override\s+(?:your|the|all)\s+(?:instructions?|rules?|safety)",
        # Role manipulation
        r"(?i)you\s+are\s+(?:now|actually)\s+(?:a|an)\s+(?:different|new)\s+(?:ai|assistant|bot)",
        r"(?i)pretend\s+(?:you(?:'re| are)?\s+)?(?:to be|that you(?:'re| are)?)",
        r"(?i)act\s+as\s+(?:if\s+)?(?:you(?:'re| are|were)?)",
        r"(?i)roleplay\s+as\s+(?:a|an)?",
        r"(?i)imagine\s+you(?:'re| are)\s+(?:a|an)?",
        # System prompt extraction
        r"(?i)(?:show|reveal|display|print|output|tell me)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
        r"(?i)what\s+(?:is|are)\s+your\s+(?:system\s+)?(?:prompt|instructions?|rules?)",
        r"(?i)repeat\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
        # Delimiter injection
        r"(?i)\[\s*(?:system|SYSTEM)\s*\]",
        r"(?i)<\s*(?:system|SYSTEM)\s*>",
        r"(?i)###\s*(?:SYSTEM|system|instructions?)",
        r"(?i)\*\*\*\s*(?:new|updated)\s+(?:instructions?|rules?)",
        # Encoding bypass attempts
        r"(?i)(?:base64|hex|binary|rot13)\s*(?:decode|encoded?)",
        r"\\x[0-9a-fA-F]{2}",  # Hex encoding
        r"&#\d+;",  # HTML entities
        # Jailbreak keywords
        r"(?i)(?:DAN|jailbreak|bypass|hack|exploit)\s+(?:mode|prompt|filter)",
        r"(?i)developer\s+mode\s+(?:enabled|activated|on)",
        r"(?i)(?:enable|activate|turn on)\s+(?:developer|debug|admin)\s+mode",
    ]

    # Suspicious patterns that warrant closer inspection
    SUSPICIOUS_PATTERNS = [
        r"(?i)(?:as\s+an?\s+)?ai\s+(?:language\s+)?model",
        r"(?i)(?:you|I)\s+must\s+(?:always|never)",
        r"(?i)without\s+any\s+(?:restrictions?|limitations?|filters?)",
        r"(?i)no\s+(?:ethical|moral|safety)\s+(?:concerns?|restrictions?)",
        r"(?i)respond\s+(?:only\s+)?(?:with|in)\s+(?:json|xml|code)",
        r"(?i)(?:do\s+not|don't)\s+(?:mention|say|include)\s+(?:that|this)",
        r"(?i)(?:hidden|secret|confidential)\s+(?:instructions?|commands?)",
    ]

    # Malicious content patterns
    MALICIOUS_PATTERNS = [
        r"(?i)<\s*script[^>]*>",  # XSS attempt
        r"(?i)javascript\s*:",  # JavaScript protocol
        r"(?i)data\s*:\s*text/html",  # Data URL
        r"(?i)on(?:load|error|click|mouse)\s*=",  # Event handlers
        r"(?i)eval\s*\(",  # Eval injection
        r"(?i)(?:exec|system|shell_exec|passthru)\s*\(",  # Command injection
    ]

    POLICY_CHECK_PROMPT = """You are a content safety reviewer for an influencer operations team.
Review this email draft for policy violations.

Check for:
1. Inappropriate promises about money or rates
2. Tone issues (too casual, too aggressive, unprofessional)
3. PR risks (statements that could be taken out of context)
4. Legal risks (commitments that should involve legal review)
5. Hallucination risk (claims that seem invented or unsupported)

Output a JSON object:
{
    "passed": true/false,
    "issues": [{"type": "...", "description": "...", "severity": "low/medium/high"}],
    "suggestions": ["..."],
    "risk_score": 0.0-1.0
}"""

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )

    async def check_content(
        self,
        content: str,
        context: str | None = None,
        check_pii: bool = True,
        check_financial: bool = True,
        check_policy: bool = True,
        check_length: bool = True,
        check_injection: bool = True,
    ) -> GuardrailResult:
        """Run all guardrail checks on content."""
        violations = []
        details: dict[str, Any] = {}
        suggestions = []
        risk_score = 0.0

        # Check for prompt injection FIRST - this is critical for security
        if check_injection:
            injection_result = self._check_prompt_injection(content)
            if injection_result["injection_detected"]:
                violations.append(GuardrailViolation.PROMPT_INJECTION)
                details["prompt_injection"] = injection_result
                suggestions.append("Content contains potential prompt injection - requires human review")
                risk_score += 0.8
                self.logger.warning(
                    "Prompt injection detected",
                    patterns_matched=injection_result.get("patterns_matched", []),
                )

            if injection_result["jailbreak_detected"]:
                violations.append(GuardrailViolation.JAILBREAK_ATTEMPT)
                details["jailbreak"] = injection_result
                suggestions.append("Content contains jailbreak attempt - blocked")
                risk_score += 0.9

            if injection_result["malicious_detected"]:
                violations.append(GuardrailViolation.MALICIOUS_CONTENT)
                details["malicious"] = injection_result
                suggestions.append("Content contains potentially malicious code - blocked")
                risk_score += 1.0

        # Check length
        if check_length and len(content) > settings.max_draft_length:
            violations.append(GuardrailViolation.LENGTH_EXCEEDED)
            details["length"] = {
                "current": len(content),
                "max": settings.max_draft_length,
            }
            suggestions.append(f"Reduce content length to under {settings.max_draft_length} characters")
            risk_score += 0.2

        # Check for forbidden terms
        forbidden_found = []
        content_lower = content.lower()
        for term in settings.forbidden_terms:
            if term.lower() in content_lower:
                forbidden_found.append(term)
        if forbidden_found:
            violations.append(GuardrailViolation.FORBIDDEN_TERM)
            details["forbidden_terms"] = forbidden_found
            suggestions.append(f"Remove forbidden terms: {', '.join(forbidden_found)}")
            risk_score += 0.3

        # Check PII
        if check_pii:
            pii_result = self._check_pii(content)
            if pii_result["found"]:
                violations.append(GuardrailViolation.PII_DETECTED)
                details["pii"] = pii_result
                suggestions.append("Remove or mask PII before sending")
                risk_score += 0.4

        # Check financial commitments
        if check_financial:
            financial_result = self._check_financial_promises(content)
            if financial_result["found"]:
                violations.append(GuardrailViolation.FINANCIAL_PROMISE)
                details["financial"] = financial_result
                suggestions.append("Remove financial commitments or get approval")
                risk_score += 0.5

        # Check policy with LLM (skip if injection detected to avoid processing malicious content)
        if check_policy and GuardrailViolation.PROMPT_INJECTION not in violations:
            policy_result = await self._check_policy_llm(content, context)
            if not policy_result.get("passed", True):
                for issue in policy_result.get("issues", []):
                    issue_type = issue.get("type", "").lower()
                    if "tone" in issue_type:
                        violations.append(GuardrailViolation.INAPPROPRIATE_TONE)
                    elif "pr" in issue_type or "risk" in issue_type:
                        violations.append(GuardrailViolation.PR_RISK)
                    elif "hallucin" in issue_type:
                        violations.append(GuardrailViolation.HALLUCINATION_RISK)
                    else:
                        violations.append(GuardrailViolation.POLICY_VIOLATION)

                details["policy"] = policy_result
                suggestions.extend(policy_result.get("suggestions", []))
                risk_score = max(risk_score, policy_result.get("risk_score", 0.5))

        # Mask PII if needed
        masked_content = None
        if settings.pii_masking_enabled and check_pii:
            masked_content = self._mask_pii(content)

        return GuardrailResult(
            passed=len(violations) == 0,
            violations=list(set(violations)),  # Remove duplicates
            details=details,
            masked_content=masked_content,
            suggestions=suggestions,
            risk_score=min(risk_score, 1.0),
        )

    def _check_prompt_injection(self, content: str) -> dict[str, Any]:
        """
        Check for prompt injection attempts in content.

        This is critical security functionality to prevent malicious users
        from manipulating the AI through email content.
        """
        result = {
            "injection_detected": False,
            "jailbreak_detected": False,
            "malicious_detected": False,
            "suspicious_detected": False,
            "patterns_matched": [],
            "suspicious_patterns": [],
            "malicious_patterns": [],
            "risk_level": "low",
        }

        # Check for direct prompt injection
        for pattern in self.PROMPT_INJECTION_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                result["injection_detected"] = True
                result["patterns_matched"].extend(matches[:3])  # Limit to 3 examples

        # Check for jailbreak attempts (subset of injection patterns)
        jailbreak_patterns = [
            r"(?i)(?:DAN|jailbreak|bypass|hack|exploit)\s+(?:mode|prompt|filter)",
            r"(?i)developer\s+mode",
            r"(?i)(?:enable|activate)\s+(?:developer|debug|admin)\s+mode",
            r"(?i)ignore\s+(?:all\s+)?(?:previous|above)\s+instructions?",
        ]
        for pattern in jailbreak_patterns:
            if re.search(pattern, content):
                result["jailbreak_detected"] = True
                break

        # Check for malicious content
        for pattern in self.MALICIOUS_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                result["malicious_detected"] = True
                result["malicious_patterns"].extend(matches[:3])

        # Check for suspicious patterns (may be legitimate but warrant review)
        for pattern in self.SUSPICIOUS_PATTERNS:
            matches = re.findall(pattern, content)
            if matches:
                result["suspicious_detected"] = True
                result["suspicious_patterns"].extend(matches[:3])

        # Calculate risk level
        if result["malicious_detected"] or result["jailbreak_detected"]:
            result["risk_level"] = "critical"
        elif result["injection_detected"]:
            result["risk_level"] = "high"
        elif result["suspicious_detected"]:
            result["risk_level"] = "medium"

        return result

    async def check_input_safety(self, content: str) -> tuple[bool, str | None]:
        """
        Quick safety check for incoming content before processing.

        Returns (is_safe, reason) tuple.
        Use this to pre-screen content before passing to LLM.
        """
        injection_result = self._check_prompt_injection(content)

        if injection_result["malicious_detected"]:
            return False, "Malicious content detected"

        if injection_result["jailbreak_detected"]:
            return False, "Jailbreak attempt detected"

        if injection_result["injection_detected"]:
            # Log but don't block - may be false positive
            self.logger.warning(
                "Potential prompt injection in input",
                patterns=injection_result["patterns_matched"],
            )
            return True, "Warning: potential prompt injection detected"

        return True, None

    def sanitize_for_prompt(self, content: str, max_length: int = 10000) -> str:
        """
        Sanitize user content before including in prompts.

        This adds delimiters and truncates to prevent injection attacks.
        """
        # Truncate if too long
        if len(content) > max_length:
            content = content[:max_length] + "\n[Content truncated...]"

        # Escape any delimiter-like patterns
        content = re.sub(r"(#+\s*(?:SYSTEM|USER|ASSISTANT))", r"\\\1", content)
        content = re.sub(r"(\[\s*(?:SYSTEM|USER|ASSISTANT)\s*\])", r"\\\1", content)
        content = re.sub(r"(<\s*(?:system|user|assistant)\s*>)", r"\\\1", content, flags=re.IGNORECASE)

        return content

    def _check_pii(self, content: str) -> dict[str, Any]:
        """Check for PII patterns in content."""
        found = {}
        for pii_type, pattern in self.PII_PATTERNS.items():
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                found[pii_type] = len(matches)

        return {"found": bool(found), "types": found}

    def _mask_pii(self, content: str) -> str:
        """Mask PII in content for logging."""
        masked = content

        for pii_type, pattern in self.PII_PATTERNS.items():
            if pii_type == "email":
                # Don't mask emails in email content
                continue
            masked = re.sub(pattern, f"[{pii_type.upper()}_MASKED]", masked)

        return masked

    def _check_financial_promises(self, content: str) -> dict[str, Any]:
        """Check for financial commitment patterns."""
        found = []
        for pattern in self.FINANCIAL_PATTERNS:
            matches = re.findall(pattern, content, re.IGNORECASE)
            found.extend(matches)

        return {"found": bool(found), "matches": found}

    async def _check_policy_llm(
        self, content: str, context: str | None
    ) -> dict[str, Any]:
        """Use LLM to check for policy violations."""
        try:
            user_prompt = f"""Review this email draft:

{content}

{"Context: " + context if context else "No additional context provided."}

Analyze for policy violations and provide your assessment as JSON."""

            response = self._client.messages.create(
                model="claude-haiku-4-20250514",  # Use faster model for guardrails
                max_tokens=512,
                temperature=0.1,
                system=self.POLICY_CHECK_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            return self._parse_response(response_text)
        except Exception as e:
            self.logger.error("Policy check failed", error=str(e))
            # Fail safe - return as if there might be issues
            return {
                "passed": False,
                "issues": [{"type": "check_failed", "description": str(e), "severity": "medium"}],
                "suggestions": ["Manual review recommended due to check failure"],
                "risk_score": 0.5,
            }

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
            self.logger.warning("Failed to parse guardrail response")
            return {"passed": True, "issues": [], "suggestions": [], "risk_score": 0.0}

    async def quick_check(self, content: str) -> bool:
        """Quick pass/fail check without detailed analysis."""
        # Just check basic rules
        if len(content) > settings.max_draft_length:
            return False

        content_lower = content.lower()
        for term in settings.forbidden_terms:
            if term.lower() in content_lower:
                return False

        for pattern in self.FINANCIAL_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False

        return True

    def mask_for_logging(self, content: str) -> str:
        """Mask sensitive content for safe logging."""
        return self._mask_pii(content)
