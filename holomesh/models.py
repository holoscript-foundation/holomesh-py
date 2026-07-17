"""SCAFFOLD 2026-07-16 (python-competitor NMoS bridge — not yet wired into __init__).

Pydantic v2 models for the HoloMesh API surface.

Optional dependency: install with `pip install holoscript-mesh[models]`
(adds `pydantic>=2.5`). The zero-dep core client (`holomesh.client`) does
NOT import this module; nothing here changes existing published behavior.

Field-shape sources (verified against server source 2026-07-16):
- Board:  HoloScript packages/mcp-server/src/holomesh/routes/board-routes.ts
          GET /api/holomesh/team/:id/board returns {tasks: [...]} FLAT list
          with a `status` field per task ("open"|"claimed"|"done").
          Do NOT model the legacy {board: {open: [...]}} nesting.
- Teams:  .../routes/team-routes.ts (message, knowledge, presence, mode)
- Signing: ai-ecosystem/hooks/lib/holomesh-signing.mjs envelope
          {body, signature, signer_address, nonce, timestamp}
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "holomesh.models requires pydantic v2 — install with "
        "`pip install holoscript-mesh[models]` or `pip install pydantic>=2.5`"
    ) from exc


class MeshModel(BaseModel):
    """Base: accept server camelCase via aliases, keep unknown fields (API evolves)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")


# -- Board ------------------------------------------------------------------


class BoardTask(MeshModel):
    id: str
    title: str = ""
    description: str = ""
    status: str = "open"  # open | claimed | done | blocked
    priority: int = 2
    tags: list[str] = Field(default_factory=list)
    claimed_by: Optional[str] = Field(default=None, alias="claimedBy")
    claimed_by_name: Optional[str] = Field(default=None, alias="claimedByName")
    completed_by: Optional[str] = Field(default=None, alias="completedBy")
    commit: Optional[str] = None
    summary: Optional[str] = None
    verification_evidence: Optional[str] = None
    created_at: Optional[str] = Field(default=None, alias="createdAt")
    completed_at: Optional[str] = Field(default=None, alias="completedAt")


class BoardResponse(MeshModel):
    tasks: list[BoardTask] = Field(default_factory=list)
    mode: Optional[str] = None  # audit | research | build | review | ...
    objective: Optional[str] = None
    communication_style: Optional[str] = Field(
        default=None, alias="communicationStyle"
    )  # task_first | meeting_primary | balanced


# -- Messages / inbox --------------------------------------------------------


class TeamMessage(MeshModel):
    id: Optional[str] = None
    type: str = "text"  # text | meeting | handoff | ...
    content: str = ""
    sender: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    thread_id: Optional[str] = Field(default=None, alias="threadId")
    timestamp: Optional[str] = None
    read: Optional[bool] = None


class InboxResponse(MeshModel):
    unread_count: int = 0
    messages: list[TeamMessage] = Field(default_factory=list)


# -- Knowledge ----------------------------------------------------------------


class KnowledgeEntry(MeshModel):
    id: Optional[str] = None
    type: str = "wisdom"  # wisdom | pattern | gotcha | ...
    title: Optional[str] = None
    content: str = ""
    domain: str = "general"
    tags: list[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    author_name: Optional[str] = Field(default=None, alias="authorName")
    votes: Optional[int] = None
    created_at: Optional[str] = Field(default=None, alias="createdAt")


# -- Presence / suggestions ---------------------------------------------------


class PresenceSeat(MeshModel):
    agent_id: Optional[str] = Field(default=None, alias="agentId")
    name: Optional[str] = None
    ide_type: Optional[str] = Field(default=None, alias="ideType")
    status: Optional[str] = None
    last_seen: Optional[str] = Field(default=None, alias="lastSeen")


class Suggestion(MeshModel):
    id: str
    content: str = ""
    status: str = "open"
    author: Optional[str] = None
    votes: Optional[int] = None


# -- Evidence receipts (triad: intent / proof / replay) ----------------------


class EvidenceReceipt(MeshModel):
    """Secret-free evidence attachment for `done` completions and knowledge syncs.

    Mirrors the triad shape used by holomesh-py's holoscript_bridge_receipt and
    the `verification_evidence` field consumed by the board `done` PATCH.
    """

    schema_id: str = Field(
        default="holoscript.mesh-python-evidence.v1", alias="schema"
    )
    intent: str = ""
    proof: str = ""  # what was observed (test output summary, hash, URL)
    replay: str = ""  # exact command a peer can run to reproduce
    commit: Optional[str] = None
    artifacts: list[str] = Field(default_factory=list)  # paths/URLs, no secrets

    def as_verification_evidence(self) -> str:
        """Render to the single-string form the board PATCH expects."""
        parts = [f"intent: {self.intent}", f"proof: {self.proof}", f"replay: {self.replay}"]
        if self.commit:
            parts.append(f"commit: {self.commit}")
        if self.artifacts:
            parts.append("artifacts: " + ", ".join(self.artifacts))
        return " | ".join(parts)


# -- Request signing (Phase 3 mutating routes) --------------------------------


class SignedEnvelope(MeshModel):
    """EIP-191 signed request envelope — see ai-ecosystem hooks/lib/holomesh-signing.mjs.

    signature = personal_sign(canonicalize({body, nonce, timestamp}))
    """

    body: dict[str, Any]
    signature: str
    signer_address: str
    nonce: str
    timestamp: str
