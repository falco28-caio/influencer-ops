from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb
import openai
import tiktoken
from chromadb.config import Settings as ChromaSettings

from src.adapters.notion import NotionAdapter
from src.core.config import settings
from src.core.logging import get_logger


@dataclass
class RetrievedContext:
    """A piece of retrieved context."""

    content: str
    source: str
    source_type: str
    relevance_score: float
    metadata: dict[str, Any]


@dataclass
class RAGContext:
    """Combined context for draft generation."""

    sop_content: str
    conversation_history: str
    influencer_context: str
    similar_cases: list[str]
    sources: list[str] = field(default_factory=list)
    total_tokens: int = 0


class ChunkingStrategy:
    """Strategy for intelligent text chunking."""

    def __init__(
        self,
        max_tokens: int = 500,
        overlap_tokens: int = 50,
        model: str = "cl100k_base",
    ):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.encoding = tiktoken.get_encoding(model)

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self.encoding.encode(text))

    def chunk_by_sections(self, text: str, title: str = "") -> list[dict[str, Any]]:
        """Chunk text by logical sections (headers, paragraphs)."""
        chunks = []

        # Split by headers first
        header_pattern = r"^(#{1,3})\s+(.+)$"
        sections = re.split(r"(?=^#{1,3}\s)", text, flags=re.MULTILINE)

        current_section = title
        for section in sections:
            section = section.strip()
            if not section:
                continue

            # Check if this starts with a header
            header_match = re.match(header_pattern, section, re.MULTILINE)
            if header_match:
                current_section = header_match.group(2).strip()

            # If section is small enough, keep as one chunk
            if self.count_tokens(section) <= self.max_tokens:
                chunks.append({
                    "content": section,
                    "section": current_section,
                    "type": "section",
                })
            else:
                # Split into paragraphs
                paragraphs = section.split("\n\n")
                current_chunk = []
                current_tokens = 0

                for para in paragraphs:
                    para_tokens = self.count_tokens(para)

                    if current_tokens + para_tokens > self.max_tokens and current_chunk:
                        chunks.append({
                            "content": "\n\n".join(current_chunk),
                            "section": current_section,
                            "type": "paragraph_group",
                        })
                        # Keep overlap
                        overlap_text = current_chunk[-1] if current_chunk else ""
                        current_chunk = [overlap_text] if overlap_text else []
                        current_tokens = self.count_tokens(overlap_text)

                    current_chunk.append(para)
                    current_tokens += para_tokens

                if current_chunk:
                    chunks.append({
                        "content": "\n\n".join(current_chunk),
                        "section": current_section,
                        "type": "paragraph_group",
                    })

        return chunks

    def chunk_conversation(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Chunk conversation history preserving message boundaries."""
        chunks = []
        current_chunk = []
        current_tokens = 0

        for msg in messages:
            direction = msg.get("direction", "unknown")
            content = msg.get("content", "")
            formatted = f"[{direction.upper()}]: {content}"
            msg_tokens = self.count_tokens(formatted)

            if current_tokens + msg_tokens > self.max_tokens and current_chunk:
                chunks.append({
                    "content": "\n\n".join(current_chunk),
                    "type": "conversation",
                    "message_count": len(current_chunk),
                })
                current_chunk = []
                current_tokens = 0

            current_chunk.append(formatted)
            current_tokens += msg_tokens

        if current_chunk:
            chunks.append({
                "content": "\n\n".join(current_chunk),
                "type": "conversation",
                "message_count": len(current_chunk),
            })

        return chunks


class HybridSearcher:
    """Combines semantic and keyword search for better retrieval."""

    def __init__(self, collection: chromadb.Collection):
        self.collection = collection

    def keyword_search(
        self, query: str, n_results: int = 10
    ) -> list[tuple[str, float]]:
        """Simple keyword-based search using document metadata."""
        # Extract keywords from query
        keywords = set(re.findall(r"\b\w{4,}\b", query.lower()))

        if not keywords:
            return []

        # Get all documents (in production, use proper full-text search)
        results = self.collection.get(include=["documents", "metadatas"])

        scored_docs = []
        for i, (doc, meta) in enumerate(
            zip(results["documents"] or [], results["metadatas"] or [])
        ):
            doc_lower = doc.lower()
            title_lower = meta.get("title", "").lower()

            # Score based on keyword matches
            score = 0
            for kw in keywords:
                if kw in title_lower:
                    score += 2  # Title match worth more
                if kw in doc_lower:
                    score += 1

            if score > 0:
                scored_docs.append((results["ids"][i], score / len(keywords)))

        # Sort by score and return top results
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        return scored_docs[:n_results]

    def hybrid_search(
        self,
        query: str,
        query_embedding: list[float],
        n_results: int = 5,
        semantic_weight: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Combine semantic and keyword search results."""
        # Semantic search
        semantic_results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results * 2,
            include=["documents", "metadatas", "distances"],
        )

        # Keyword search
        keyword_results = self.keyword_search(query, n_results * 2)
        keyword_scores = {doc_id: score for doc_id, score in keyword_results}

        # Combine scores
        combined = {}
        for i, doc_id in enumerate(semantic_results["ids"][0]):
            semantic_score = 1 - semantic_results["distances"][0][i]  # Convert distance to similarity
            keyword_score = keyword_scores.get(doc_id, 0)

            combined_score = (
                semantic_weight * semantic_score
                + (1 - semantic_weight) * keyword_score
            )

            combined[doc_id] = {
                "id": doc_id,
                "document": semantic_results["documents"][0][i],
                "metadata": semantic_results["metadatas"][0][i],
                "semantic_score": semantic_score,
                "keyword_score": keyword_score,
                "combined_score": combined_score,
            }

        # Add keyword-only results
        for doc_id, kw_score in keyword_results:
            if doc_id not in combined:
                # Need to fetch the document
                doc_data = self.collection.get(ids=[doc_id], include=["documents", "metadatas"])
                if doc_data["documents"]:
                    combined[doc_id] = {
                        "id": doc_id,
                        "document": doc_data["documents"][0],
                        "metadata": doc_data["metadatas"][0],
                        "semantic_score": 0,
                        "keyword_score": kw_score,
                        "combined_score": (1 - semantic_weight) * kw_score,
                    }

        # Sort by combined score and return top results
        sorted_results = sorted(
            combined.values(), key=lambda x: x["combined_score"], reverse=True
        )
        return sorted_results[:n_results]


class RAGService:
    """Enhanced RAG service with intelligent retrieval."""

    COLLECTION_SOPS = "sops"
    COLLECTION_TEMPLATES = "templates"
    COLLECTION_CONVERSATIONS = "conversations"
    COLLECTION_CASES = "similar_cases"

    def __init__(self) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._chroma_client: chromadb.Client | None = None
        self._openai_client: openai.OpenAI | None = None
        self._notion_adapter: NotionAdapter | None = None
        self._chunker = ChunkingStrategy()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the RAG service."""
        if self._initialized:
            return

        # Initialize ChromaDB
        persist_dir = Path(settings.chroma_persist_directory)
        persist_dir.mkdir(parents=True, exist_ok=True)

        self._chroma_client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # Initialize OpenAI for embeddings
        self._openai_client = openai.OpenAI(
            api_key=settings.openai_api_key.get_secret_value()
        )

        # Initialize Notion adapter
        self._notion_adapter = NotionAdapter()
        await self._notion_adapter.initialize()

        self._initialized = True
        self.logger.info("RAG service initialized")

    async def close(self) -> None:
        """Close the RAG service."""
        if self._notion_adapter:
            await self._notion_adapter.close()
        self._initialized = False
        self.logger.info("RAG service closed")

    def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector for text."""
        if not self._openai_client:
            raise RuntimeError("RAG service not initialized")

        # Truncate if too long
        max_tokens = 8000
        if self._chunker.count_tokens(text) > max_tokens:
            tokens = self._chunker.encoding.encode(text)[:max_tokens]
            text = self._chunker.encoding.decode(tokens)

        response = self._openai_client.embeddings.create(
            model=settings.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    def _get_collection(self, name: str) -> chromadb.Collection:
        """Get or create a ChromaDB collection."""
        if not self._chroma_client:
            raise RuntimeError("RAG service not initialized")

        return self._chroma_client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def _generate_doc_id(self, content: str, source: str) -> str:
        """Generate a unique document ID."""
        hash_input = f"{source}:{content[:100]}"
        return hashlib.md5(hash_input.encode()).hexdigest()

    async def sync_sops_from_notion(self) -> int:
        """Sync SOPs from Notion with intelligent chunking."""
        if not self._notion_adapter:
            raise RuntimeError("RAG service not initialized")

        self.logger.info("Syncing SOPs from Notion")

        # Query all SOPs from Notion
        pages = await self._notion_adapter.query_database()

        collection = self._get_collection(self.COLLECTION_SOPS)

        # Track existing IDs for cleanup
        new_ids = set()
        count = 0

        for page in pages:
            if not page.content.strip():
                continue

            # Use intelligent chunking
            chunks = self._chunker.chunk_by_sections(page.content, page.title)

            for i, chunk in enumerate(chunks):
                doc_id = self._generate_doc_id(chunk["content"], page.page_id)
                new_ids.add(doc_id)

                embedding = self._get_embedding(chunk["content"])

                # Upsert to handle updates
                collection.upsert(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[chunk["content"]],
                    metadatas=[
                        {
                            "page_id": page.page_id,
                            "title": page.title,
                            "section": chunk.get("section", ""),
                            "category": page.category or "",
                            "tags": ",".join(page.tags),
                            "chunk_index": i,
                            "chunk_type": chunk.get("type", "unknown"),
                            "last_synced": datetime.utcnow().isoformat(),
                        }
                    ],
                )
                count += 1

        self.logger.info("SOP sync complete", document_count=count)
        return count

    async def retrieve_context(
        self,
        query: str,
        collection_name: str = COLLECTION_SOPS,
        n_results: int = 5,
        min_score: float = 0.5,
        use_hybrid: bool = True,
    ) -> list[RetrievedContext]:
        """Retrieve relevant context using hybrid search."""
        collection = self._get_collection(collection_name)

        if collection.count() == 0:
            self.logger.warning("Collection is empty", collection=collection_name)
            return []

        query_embedding = self._get_embedding(query)

        if use_hybrid:
            searcher = HybridSearcher(collection)
            results = searcher.hybrid_search(
                query=query,
                query_embedding=query_embedding,
                n_results=n_results,
            )

            contexts = []
            for result in results:
                if result["combined_score"] >= min_score:
                    contexts.append(
                        RetrievedContext(
                            content=result["document"],
                            source=result["metadata"].get("title", "Unknown"),
                            source_type=collection_name,
                            relevance_score=result["combined_score"],
                            metadata=result["metadata"],
                        )
                    )
            return contexts
        else:
            # Pure semantic search
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )

            contexts = []
            for i, (doc, metadata, distance) in enumerate(
                zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            ):
                similarity = 1 - distance
                if similarity >= min_score:
                    contexts.append(
                        RetrievedContext(
                            content=doc,
                            source=metadata.get("title", "Unknown"),
                            source_type=collection_name,
                            relevance_score=similarity,
                            metadata=metadata,
                        )
                    )

            return sorted(contexts, key=lambda x: x.relevance_score, reverse=True)

    async def retrieve_sop_for_intent(
        self,
        intent: str,
        additional_context: str = "",
    ) -> str:
        """Retrieve SOP content relevant to a specific intent."""
        # Build a rich query
        intent_descriptions = {
            "collab_inquiry": "collaboration partnership brand deal campaign",
            "rate_negotiation": "rates pricing fees compensation budget payment terms",
            "schedule_meeting": "scheduling meeting call calendar availability booking",
            "payment_info": "payment invoice banking wire transfer ACH",
            "contract_question": "contract agreement terms legal signature",
            "content_delivery": "content creation deliverables assets posting timeline",
            "follow_up": "follow up checking in response pending waiting",
            "general_question": "general inquiry help information",
        }

        intent_terms = intent_descriptions.get(intent, intent.replace("_", " "))
        query = f"SOP procedure for handling {intent_terms} {additional_context}".strip()

        contexts = await self.retrieve_context(
            query=query,
            collection_name=self.COLLECTION_SOPS,
            n_results=5,
            min_score=0.4,
        )

        if not contexts:
            return ""

        # Deduplicate by source
        seen_sources = set()
        unique_contexts = []
        for ctx in contexts:
            if ctx.source not in seen_sources:
                seen_sources.add(ctx.source)
                unique_contexts.append(ctx)

        # Format SOP content
        sop_parts = []
        for ctx in unique_contexts[:3]:  # Top 3 unique sources
            section = ctx.metadata.get("section", "")
            header = f"## {ctx.source}"
            if section and section != ctx.source:
                header += f" > {section}"
            sop_parts.append(f"{header}\n{ctx.content}")

        return "\n\n---\n\n".join(sop_parts)

    async def build_draft_context(
        self,
        intent: str,
        original_email: str,
        influencer_info: dict[str, Any] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> RAGContext:
        """Build comprehensive context for draft generation."""
        sources = []
        total_tokens = 0

        # 1. Get SOP content
        sop_content = await self.retrieve_sop_for_intent(intent, original_email[:200])
        if sop_content:
            sources.append(f"SOPs for {intent}")
            total_tokens += self._chunker.count_tokens(sop_content)

        # 2. Format conversation history
        conv_history = ""
        if conversation_history:
            formatted_msgs = []
            for msg in conversation_history[-10:]:  # Last 10 messages
                direction = msg.get("direction", "unknown")
                content = msg.get("content", "")[:500]  # Truncate long messages
                formatted_msgs.append(f"[{direction.upper()}]: {content}")
            conv_history = "\n\n".join(formatted_msgs)
            total_tokens += self._chunker.count_tokens(conv_history)

        # 3. Format influencer context
        inf_context = ""
        if influencer_info:
            inf_parts = [
                f"Name: {influencer_info.get('name', 'Unknown')}",
                f"Status: {influencer_info.get('status', 'Unknown')}",
                f"Risk Level: {influencer_info.get('risk_level', 'Unknown')}",
            ]
            if influencer_info.get("notes"):
                inf_parts.append(f"Notes: {influencer_info['notes']}")
            inf_context = "\n".join(inf_parts)
            total_tokens += self._chunker.count_tokens(inf_context)

        # 4. Find similar past cases
        similar_cases = []
        case_contexts = await self.retrieve_context(
            query=original_email[:500],
            collection_name=self.COLLECTION_CASES,
            n_results=2,
            min_score=0.6,
        )
        for ctx in case_contexts:
            similar_cases.append(ctx.content)
            sources.append(f"Similar case: {ctx.source}")

        return RAGContext(
            sop_content=sop_content,
            conversation_history=conv_history,
            influencer_context=inf_context,
            similar_cases=similar_cases,
            sources=sources,
            total_tokens=total_tokens,
        )

    async def add_successful_case(
        self,
        conversation_id: str,
        intent: str,
        original_email: str,
        response: str,
        outcome: str,
    ) -> None:
        """Store a successful case for future reference."""
        collection = self._get_collection(self.COLLECTION_CASES)

        case_content = f"""Intent: {intent}
Original Email:
{original_email[:500]}

Successful Response:
{response[:500]}

Outcome: {outcome}"""

        doc_id = self._generate_doc_id(case_content, conversation_id)
        embedding = self._get_embedding(case_content)

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[case_content],
            metadatas=[
                {
                    "conversation_id": conversation_id,
                    "intent": intent,
                    "outcome": outcome,
                    "created_at": datetime.utcnow().isoformat(),
                }
            ],
        )

    async def add_conversation_history(
        self,
        conversation_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Add conversation history to the vector store."""
        collection = self._get_collection(self.COLLECTION_CONVERSATIONS)

        chunks = self._chunker.chunk_conversation(messages)

        for i, chunk in enumerate(chunks):
            doc_id = f"{conversation_id}_{i}"
            embedding = self._get_embedding(chunk["content"])

            collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[chunk["content"]],
                metadatas=[
                    {
                        "conversation_id": conversation_id,
                        "chunk_index": i,
                        "message_count": chunk.get("message_count", 0),
                        "type": chunk.get("type", "conversation"),
                    }
                ],
            )

    async def search_templates(
        self,
        intent: str,
        context: str = "",
    ) -> list[RetrievedContext]:
        """Search for relevant email templates."""
        query = f"email template for {intent.replace('_', ' ')} {context}"
        return await self.retrieve_context(
            query=query,
            collection_name=self.COLLECTION_TEMPLATES,
            n_results=3,
            min_score=0.5,
        )

    async def add_template(
        self,
        name: str,
        intent: str,
        content: str,
        tags: list[str] | None = None,
    ) -> None:
        """Add an email template to the vector store."""
        collection = self._get_collection(self.COLLECTION_TEMPLATES)

        doc_id = self._generate_doc_id(content, name)
        embedding = self._get_embedding(content)

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[
                {
                    "name": name,
                    "intent": intent,
                    "tags": ",".join(tags) if tags else "",
                    "created_at": datetime.utcnow().isoformat(),
                }
            ],
        )

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about the RAG collections."""
        stats = {}
        for collection_name in [
            self.COLLECTION_SOPS,
            self.COLLECTION_TEMPLATES,
            self.COLLECTION_CONVERSATIONS,
            self.COLLECTION_CASES,
        ]:
            try:
                collection = self._get_collection(collection_name)
                stats[collection_name] = {
                    "count": collection.count(),
                }
            except Exception:
                stats[collection_name] = {"count": 0, "error": "Failed to get stats"}

        return stats
