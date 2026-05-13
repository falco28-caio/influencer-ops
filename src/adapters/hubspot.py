"""
Enhanced HubSpot Adapter with full CRM integration.

Supports:
- Contacts (Influencers)
- Deals (Campaigns/Partnerships)
- Activities (Emails, Calls, Notes)
- Properties sync
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from hubspot import HubSpot
from hubspot.crm.contacts import SimplePublicObjectInput
from hubspot.crm.deals import SimplePublicObjectInput as DealInput
from tenacity import retry, stop_after_attempt, wait_exponential

from src.adapters.base import BaseAdapter
from src.core.config import settings
from src.models.influencer import InfluencerStatus


class DealStage(StrEnum):
    """HubSpot deal stages for influencer partnerships."""

    PROSPECT = "prospect"
    OUTREACH = "outreach"
    NEGOTIATION = "negotiation"
    CONTRACT_SENT = "contract_sent"
    CONTRACT_SIGNED = "contract_signed"
    CONTENT_IN_PROGRESS = "content_in_progress"
    CONTENT_DELIVERED = "content_delivered"
    PAYMENT_PENDING = "payment_pending"
    COMPLETED = "completed"
    LOST = "lost"


@dataclass
class HubSpotContact:
    """Represents a HubSpot contact."""

    hubspot_id: str
    email: str
    first_name: str | None
    last_name: str | None
    company: str | None
    status: str | None
    deal_stage: str | None
    last_activity: datetime | None
    properties: dict[str, Any]

    @property
    def full_name(self) -> str:
        parts = [self.first_name or "", self.last_name or ""]
        return " ".join(p for p in parts if p).strip() or self.email.split("@")[0]


@dataclass
class HubSpotDeal:
    """Represents a HubSpot deal."""

    hubspot_id: str
    name: str
    stage: str
    amount: float | None
    close_date: datetime | None
    contact_ids: list[str]
    properties: dict[str, Any]


@dataclass
class HubSpotActivity:
    """Represents a HubSpot activity (email, call, note)."""

    hubspot_id: str
    activity_type: str  # EMAIL, CALL, NOTE, MEETING
    subject: str | None
    body: str | None
    timestamp: datetime
    contact_ids: list[str]
    deal_ids: list[str]
    properties: dict[str, Any]


@dataclass
class SyncResult:
    """Result of a sync operation."""

    contacts_created: int = 0
    contacts_updated: int = 0
    deals_created: int = 0
    deals_updated: int = 0
    activities_synced: int = 0
    errors: list[str] = field(default_factory=list)


class HubSpotAdapter(BaseAdapter):
    """Enhanced adapter for HubSpot CRM operations."""

    # Custom property mappings
    CONTACT_PROPERTIES = [
        "email",
        "firstname",
        "lastname",
        "company",
        "hs_lead_status",
        "lifecyclestage",
        "phone",
        "instagram_handle",
        "twitter_handle",
        "tiktok_handle",
        "youtube_channel",
        "follower_count",
        "engagement_rate",
        "influencer_tier",
        "content_categories",
        "risk_level",
        "notes_last_updated",
    ]

    DEAL_PROPERTIES = [
        "dealname",
        "dealstage",
        "amount",
        "closedate",
        "campaign_name",
        "content_type",
        "deliverables",
        "contract_status",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._client: HubSpot | None = None

    async def initialize(self) -> None:
        """Initialize HubSpot client."""
        api_key = settings.hubspot_api_key.get_secret_value()
        if not api_key:
            raise ValueError("HubSpot API key not configured")

        self._client = HubSpot(access_token=api_key)
        self._initialized = True
        self.logger.info("HubSpot adapter initialized")

    async def health_check(self) -> bool:
        """Check HubSpot connection."""
        if not self._client:
            return False
        try:
            self._client.crm.contacts.basic_api.get_page(limit=1)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close HubSpot adapter."""
        self._client = None
        self._initialized = False
        self.logger.info("HubSpot adapter closed")

    # ===================
    # Contact Operations
    # ===================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def get_contact_by_email(self, email: str) -> HubSpotContact | None:
        """Get a contact by email address."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            filter_groups = [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }
                    ]
                }
            ]

            result = self._client.crm.contacts.search_api.do_search(
                public_object_search_request={
                    "filterGroups": filter_groups,
                    "properties": self.CONTACT_PROPERTIES,
                    "limit": 1,
                }
            )

            if result.results:
                return self._parse_contact(result.results[0])
            return None
        except Exception as e:
            self.logger.error("Failed to get contact by email", email=email, error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def get_contact(self, hubspot_id: str) -> HubSpotContact | None:
        """Get a contact by HubSpot ID."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            contact = self._client.crm.contacts.basic_api.get_by_id(
                contact_id=hubspot_id,
                properties=self.CONTACT_PROPERTIES,
            )
            return self._parse_contact(contact)
        except Exception as e:
            self.logger.error("Failed to get contact", hubspot_id=hubspot_id, error=str(e))
            return None

    def _parse_contact(self, contact: Any) -> HubSpotContact:
        """Parse a HubSpot contact object."""
        props = contact.properties or {}

        last_activity = None
        if props.get("notes_last_updated"):
            try:
                last_activity = datetime.fromisoformat(
                    props["notes_last_updated"].replace("Z", "+00:00")
                )
            except Exception:
                pass

        return HubSpotContact(
            hubspot_id=contact.id,
            email=props.get("email", ""),
            first_name=props.get("firstname"),
            last_name=props.get("lastname"),
            company=props.get("company"),
            status=props.get("hs_lead_status"),
            deal_stage=props.get("lifecyclestage"),
            last_activity=last_activity,
            properties=props,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def list_contacts(
        self,
        limit: int = 100,
        after: str | None = None,
        properties: list[str] | None = None,
        filters: list[dict[str, Any]] | None = None,
    ) -> tuple[list[HubSpotContact], str | None]:
        """List contacts with pagination and optional filtering."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            if filters:
                # Use search API for filtered queries
                result = self._client.crm.contacts.search_api.do_search(
                    public_object_search_request={
                        "filterGroups": [{"filters": filters}],
                        "properties": properties or self.CONTACT_PROPERTIES,
                        "limit": limit,
                        "after": after,
                    }
                )
            else:
                # Use basic API for simple listing
                result = self._client.crm.contacts.basic_api.get_page(
                    limit=limit,
                    after=after,
                    properties=properties or self.CONTACT_PROPERTIES,
                )

            contacts = [self._parse_contact(c) for c in result.results]
            next_page = result.paging.next.after if result.paging else None

            return contacts, next_page
        except Exception as e:
            self.logger.error("Failed to list contacts", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def create_contact(
        self,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> HubSpotContact:
        """Create a new contact."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        props = {"email": email}
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        if properties:
            props.update(properties)

        try:
            contact = self._client.crm.contacts.basic_api.create(
                simple_public_object_input=SimplePublicObjectInput(properties=props)
            )
            self.logger.info("Contact created", hubspot_id=contact.id, email=email)
            return self._parse_contact(contact)
        except Exception as e:
            self.logger.error("Failed to create contact", email=email, error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def update_contact(
        self,
        hubspot_id: str,
        properties: dict[str, Any],
    ) -> HubSpotContact:
        """Update a contact's properties."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            contact = self._client.crm.contacts.basic_api.update(
                contact_id=hubspot_id,
                simple_public_object_input=SimplePublicObjectInput(properties=properties),
            )
            self.logger.info(
                "Contact updated", hubspot_id=hubspot_id, properties=list(properties.keys())
            )
            return self._parse_contact(contact)
        except Exception as e:
            self.logger.error("Failed to update contact", hubspot_id=hubspot_id, error=str(e))
            raise

    # =================
    # Deal Operations
    # =================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def get_deal(self, hubspot_id: str) -> HubSpotDeal | None:
        """Get a deal by HubSpot ID."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            deal = self._client.crm.deals.basic_api.get_by_id(
                deal_id=hubspot_id,
                properties=self.DEAL_PROPERTIES,
                associations=["contacts"],
            )
            return self._parse_deal(deal)
        except Exception as e:
            self.logger.error("Failed to get deal", hubspot_id=hubspot_id, error=str(e))
            return None

    def _parse_deal(self, deal: Any) -> HubSpotDeal:
        """Parse a HubSpot deal object."""
        props = deal.properties or {}

        close_date = None
        if props.get("closedate"):
            try:
                close_date = datetime.fromisoformat(props["closedate"].replace("Z", "+00:00"))
            except Exception:
                pass

        amount = None
        if props.get("amount"):
            try:
                amount = float(props["amount"])
            except Exception:
                pass

        # Get associated contact IDs
        contact_ids = []
        if hasattr(deal, "associations") and deal.associations:
            contacts = deal.associations.get("contacts", {})
            if contacts and hasattr(contacts, "results"):
                contact_ids = [str(c.id) for c in contacts.results]

        return HubSpotDeal(
            hubspot_id=deal.id,
            name=props.get("dealname", ""),
            stage=props.get("dealstage", ""),
            amount=amount,
            close_date=close_date,
            contact_ids=contact_ids,
            properties=props,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def list_deals_for_contact(
        self,
        contact_id: str,
    ) -> list[HubSpotDeal]:
        """List all deals associated with a contact."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            # Get associations
            associations = self._client.crm.contacts.associations_api.get_all(
                contact_id=contact_id,
                to_object_type="deals",
            )

            deals = []
            for assoc in associations.results:
                deal = await self.get_deal(str(assoc.id))
                if deal:
                    deals.append(deal)

            return deals
        except Exception as e:
            self.logger.error(
                "Failed to list deals for contact", contact_id=contact_id, error=str(e)
            )
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def create_deal(
        self,
        name: str,
        stage: DealStage,
        contact_ids: list[str],
        amount: float | None = None,
        properties: dict[str, Any] | None = None,
    ) -> HubSpotDeal:
        """Create a new deal."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        props: dict[str, Any] = {
            "dealname": name,
            "dealstage": stage.value,
        }
        if amount is not None:
            props["amount"] = str(amount)
        if properties:
            props.update(properties)

        try:
            deal = self._client.crm.deals.basic_api.create(
                simple_public_object_input=DealInput(properties=props)
            )

            # Associate with contacts
            for contact_id in contact_ids:
                self._client.crm.deals.associations_api.create(
                    deal_id=deal.id,
                    to_object_type="contacts",
                    to_object_id=contact_id,
                    association_type="deal_to_contact",
                )

            self.logger.info("Deal created", hubspot_id=deal.id, name=name)
            return self._parse_deal(deal)
        except Exception as e:
            self.logger.error("Failed to create deal", name=name, error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def update_deal_stage(
        self,
        hubspot_id: str,
        stage: DealStage,
    ) -> HubSpotDeal:
        """Update a deal's stage."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            deal = self._client.crm.deals.basic_api.update(
                deal_id=hubspot_id,
                simple_public_object_input=DealInput(properties={"dealstage": stage.value}),
            )
            self.logger.info("Deal stage updated", hubspot_id=hubspot_id, stage=stage.value)
            return self._parse_deal(deal)
        except Exception as e:
            self.logger.error("Failed to update deal stage", hubspot_id=hubspot_id, error=str(e))
            raise

    # =====================
    # Activity Operations
    # =====================

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def log_email_activity(
        self,
        contact_id: str,
        subject: str,
        body: str,
        direction: str = "OUTBOUND",
        deal_id: str | None = None,
    ) -> str:
        """Log an email activity in HubSpot."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            # Create engagement
            {
                "engagement": {
                    "active": True,
                    "type": "EMAIL",
                    "timestamp": int(datetime.utcnow().timestamp() * 1000),
                },
                "associations": {
                    "contactIds": [int(contact_id)],
                    "dealIds": [int(deal_id)] if deal_id else [],
                },
                "metadata": {
                    "from": {"email": "team@company.com"},
                    "to": [{"email": ""}],
                    "subject": subject,
                    "text": body,
                },
            }

            result = self._client.crm.objects.communications.basic_api.create(
                simple_public_object_input=SimplePublicObjectInput(
                    properties={
                        "hs_communication_channel_type": "EMAIL",
                        "hs_communication_logged_from": "CRM",
                        "hs_communication_body": body,
                    }
                )
            )

            self.logger.info(
                "Email activity logged",
                contact_id=contact_id,
                subject=subject,
            )
            return result.id
        except Exception as e:
            self.logger.error("Failed to log email activity", error=str(e))
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def add_note(
        self,
        contact_id: str,
        note_body: str,
        deal_id: str | None = None,
    ) -> str:
        """Add a note to a contact."""
        if not self._client:
            raise RuntimeError("HubSpot adapter not initialized")

        try:
            note = self._client.crm.objects.notes.basic_api.create(
                simple_public_object_input=SimplePublicObjectInput(
                    properties={
                        "hs_note_body": note_body,
                        "hs_timestamp": str(int(datetime.utcnow().timestamp() * 1000)),
                    }
                )
            )

            # Associate with contact
            self._client.crm.objects.notes.associations_api.create(
                note_id=note.id,
                to_object_type="contacts",
                to_object_id=contact_id,
                association_type="note_to_contact",
            )

            if deal_id:
                self._client.crm.objects.notes.associations_api.create(
                    note_id=note.id,
                    to_object_type="deals",
                    to_object_id=deal_id,
                    association_type="note_to_deal",
                )

            self.logger.info("Note added", contact_id=contact_id)
            return note.id
        except Exception as e:
            self.logger.error("Failed to add note", contact_id=contact_id, error=str(e))
            raise

    # =====================
    # Sync Operations
    # =====================

    async def full_sync(
        self,
        session: Any,  # AsyncSession
    ) -> SyncResult:
        """
        Perform a full sync between HubSpot and local database.

        This syncs:
        - All contacts -> Influencers
        - All deals associated with contacts
        """
        from sqlalchemy import select

        from src.models import Influencer

        result = SyncResult()

        try:
            # Sync contacts
            after = None
            while True:
                contacts, after = await self.list_contacts(limit=100, after=after)

                for contact in contacts:
                    try:
                        # Check if influencer exists
                        query = select(Influencer).where(
                            Influencer.hubspot_id == contact.hubspot_id
                        )
                        db_result = await session.execute(query)
                        influencer = db_result.scalar_one_or_none()

                        if influencer:
                            # Update existing
                            influencer.name = contact.full_name
                            influencer.email = contact.email
                            if contact.properties.get("instagram_handle"):
                                influencer.instagram_handle = contact.properties["instagram_handle"]
                            if contact.properties.get("twitter_handle"):
                                influencer.twitter_handle = contact.properties["twitter_handle"]
                            result.contacts_updated += 1
                        else:
                            # Create new
                            influencer = Influencer(
                                hubspot_id=contact.hubspot_id,
                                name=contact.full_name,
                                email=contact.email,
                                instagram_handle=contact.properties.get("instagram_handle"),
                                twitter_handle=contact.properties.get("twitter_handle"),
                            )
                            session.add(influencer)
                            result.contacts_created += 1

                    except Exception as e:
                        result.errors.append(f"Contact {contact.hubspot_id}: {str(e)}")

                if not after:
                    break

            await session.commit()
            self.logger.info(
                "Full sync complete",
                contacts_created=result.contacts_created,
                contacts_updated=result.contacts_updated,
                errors=len(result.errors),
            )

        except Exception as e:
            result.errors.append(f"Sync error: {str(e)}")
            self.logger.error("Full sync failed", error=str(e))

        return result

    async def sync_influencer_to_hubspot(
        self,
        influencer: Any,  # Influencer model
    ) -> str | None:
        """Sync a local influencer to HubSpot."""
        properties = {
            "firstname": influencer.name.split()[0] if influencer.name else "",
            "lastname": (
                " ".join(influencer.name.split()[1:])
                if influencer.name and len(influencer.name.split()) > 1
                else ""
            ),
            "hs_lead_status": self._status_to_hubspot(influencer.status),
        }

        if influencer.instagram_handle:
            properties["instagram_handle"] = influencer.instagram_handle
        if influencer.twitter_handle:
            properties["twitter_handle"] = influencer.twitter_handle
        if influencer.notes:
            properties["notes_last_updated"] = datetime.utcnow().isoformat()

        if influencer.hubspot_id:
            await self.update_contact(influencer.hubspot_id, properties)
            return influencer.hubspot_id
        else:
            contact = await self.create_contact(
                email=influencer.email,
                properties=properties,
            )
            return contact.hubspot_id

    def _status_to_hubspot(self, status: InfluencerStatus) -> str:
        """Map influencer status to HubSpot lead status."""
        mapping = {
            InfluencerStatus.PROSPECT: "NEW",
            InfluencerStatus.ACTIVE: "OPEN",
            InfluencerStatus.INACTIVE: "UNQUALIFIED",
            InfluencerStatus.BLACKLISTED: "BAD_TIMING",
        }
        return mapping.get(status, "NEW")

    async def get_contact_timeline(
        self,
        contact_id: str,
        limit: int = 50,
    ) -> list[HubSpotActivity]:
        """Get activity timeline for a contact."""
        # This would fetch engagements/activities
        # Simplified implementation
        return []
