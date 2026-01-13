from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from src.adapters.base import BaseAdapter
from src.core.config import settings


@dataclass
class EmailMessage:
    """Represents an email message."""

    message_id: str
    thread_id: str
    subject: str
    sender: str
    recipients: list[str]
    body: str
    body_html: str | None
    timestamp: datetime
    labels: list[str]
    is_unread: bool
    headers: dict[str, str]
    attachments: list[dict[str, Any]]


class GmailAdapter(BaseAdapter):
    """Adapter for Gmail API operations."""

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    TOKEN_FILE = "data/gmail_token.json"
    CREDENTIALS_FILE = "data/gmail_credentials.json"

    def __init__(self) -> None:
        super().__init__()
        self._service: Resource | None = None
        self._credentials: Credentials | None = None

    async def initialize(self) -> None:
        """Initialize Gmail API connection."""
        self._credentials = await self._get_credentials()
        self._service = build("gmail", "v1", credentials=self._credentials)
        self._initialized = True
        self.logger.info("Gmail adapter initialized")

    async def _get_credentials(self) -> Credentials:
        """Get or refresh OAuth credentials."""
        creds: Credentials | None = None
        token_path = Path(self.TOKEN_FILE)

        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds_path = Path(self.CREDENTIALS_FILE)
                if not creds_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials file not found at {self.CREDENTIALS_FILE}. "
                        "Please download OAuth credentials from Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(creds_path), self.SCOPES
                )
                creds = flow.run_local_server(port=0)

            token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(token_path, "w") as token:
                token.write(creds.to_json())

        return creds

    async def health_check(self) -> bool:
        """Check Gmail API connection."""
        if not self._service:
            return False
        try:
            self._service.users().getProfile(userId="me").execute()
            return True
        except HttpError:
            return False

    async def close(self) -> None:
        """Close Gmail adapter."""
        self._service = None
        self._initialized = False
        self.logger.info("Gmail adapter closed")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def list_messages(
        self,
        query: str = "is:unread",
        max_results: int = 10,
        label_ids: list[str] | None = None,
    ) -> list[EmailMessage]:
        """List messages matching a query."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        try:
            request_params: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": max_results,
            }
            if label_ids:
                request_params["labelIds"] = label_ids

            results = self._service.users().messages().list(**request_params).execute()
            messages = results.get("messages", [])

            email_messages = []
            for msg in messages:
                email = await self.get_message(msg["id"])
                if email:
                    email_messages.append(email)

            return email_messages
        except HttpError as e:
            self.logger.error("Failed to list messages", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def get_message(self, message_id: str) -> EmailMessage | None:
        """Get a single message by ID."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        try:
            msg = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            return self._parse_message(msg)
        except HttpError as e:
            self.logger.error("Failed to get message", message_id=message_id, error=str(e))
            return None

    def _parse_message(self, msg: dict[str, Any]) -> EmailMessage:
        """Parse a Gmail API message into EmailMessage."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

        body = ""
        body_html = None
        attachments: list[dict[str, Any]] = []

        payload = msg.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                mime_type = part.get("mimeType", "")
                if mime_type == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body = base64.urlsafe_b64decode(data).decode("utf-8")
                elif mime_type == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body_html = base64.urlsafe_b64decode(data).decode("utf-8")
                elif part.get("filename"):
                    attachments.append({
                        "filename": part["filename"],
                        "mimeType": mime_type,
                        "size": part.get("body", {}).get("size", 0),
                        "attachmentId": part.get("body", {}).get("attachmentId"),
                    })
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                body = base64.urlsafe_b64decode(data).decode("utf-8")

        timestamp = datetime.fromtimestamp(int(msg["internalDate"]) / 1000)

        return EmailMessage(
            message_id=msg["id"],
            thread_id=msg["threadId"],
            subject=headers.get("subject", "(No Subject)"),
            sender=headers.get("from", ""),
            recipients=self._parse_recipients(headers),
            body=body,
            body_html=body_html,
            timestamp=timestamp,
            labels=msg.get("labelIds", []),
            is_unread="UNREAD" in msg.get("labelIds", []),
            headers=headers,
            attachments=attachments,
        )

    def _parse_recipients(self, headers: dict[str, str]) -> list[str]:
        """Parse recipients from headers."""
        recipients = []
        for key in ["to", "cc", "bcc"]:
            if key in headers:
                recipients.extend([r.strip() for r in headers[key].split(",")])
        return recipients

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_message(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        body_html: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
    ) -> str:
        """Send an email message."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        if isinstance(to, str):
            to = [to]

        message = MIMEMultipart("alternative")
        message["to"] = ", ".join(to)
        message["subject"] = subject

        if cc:
            message["cc"] = ", ".join(cc)
        if bcc:
            message["bcc"] = ", ".join(bcc)
        if reply_to_message_id:
            message["In-Reply-To"] = reply_to_message_id
            message["References"] = reply_to_message_id

        message.attach(MIMEText(body, "plain"))
        if body_html:
            message.attach(MIMEText(body_html, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        body_dict: dict[str, Any] = {"raw": raw}
        if thread_id:
            body_dict["threadId"] = thread_id

        try:
            result = (
                self._service.users()
                .messages()
                .send(userId="me", body=body_dict)
                .execute()
            )
            self.logger.info(
                "Email sent",
                message_id=result["id"],
                to=to,
                subject=subject,
            )
            return result["id"]
        except HttpError as e:
            self.logger.error("Failed to send message", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def create_draft(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        body_html: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        """Create a draft email."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        if isinstance(to, str):
            to = [to]

        message = MIMEMultipart("alternative")
        message["to"] = ", ".join(to)
        message["subject"] = subject

        message.attach(MIMEText(body, "plain"))
        if body_html:
            message.attach(MIMEText(body_html, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        body_dict: dict[str, Any] = {"message": {"raw": raw}}
        if thread_id:
            body_dict["message"]["threadId"] = thread_id

        try:
            result = (
                self._service.users()
                .drafts()
                .create(userId="me", body=body_dict)
                .execute()
            )
            self.logger.info(
                "Draft created",
                draft_id=result["id"],
                to=to,
                subject=subject,
            )
            return result["id"]
        except HttpError as e:
            self.logger.error("Failed to create draft", error=str(e))
            raise

    async def mark_as_read(self, message_id: str) -> None:
        """Mark a message as read."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        try:
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except HttpError as e:
            self.logger.error("Failed to mark message as read", error=str(e))
            raise

    async def add_label(self, message_id: str, label_id: str) -> None:
        """Add a label to a message."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        try:
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": [label_id]},
            ).execute()
        except HttpError as e:
            self.logger.error("Failed to add label", error=str(e))
            raise

    async def get_thread(self, thread_id: str) -> list[EmailMessage]:
        """Get all messages in a thread."""
        if not self._service:
            raise RuntimeError("Gmail adapter not initialized")

        try:
            thread = (
                self._service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            return [self._parse_message(msg) for msg in thread.get("messages", [])]
        except HttpError as e:
            self.logger.error("Failed to get thread", thread_id=thread_id, error=str(e))
            raise
