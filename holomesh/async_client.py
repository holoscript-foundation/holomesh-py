"""SCAFFOLD 2026-07-16 (python-competitor NMoS bridge — not yet wired into __init__).

Async HoloMesh client (httpx) with per-endpoint auth-key selection and
optional Phase 3 request signing.

Optional dependency: `pip install holoscript-mesh[async]` (httpx>=0.27).
The zero-dep sync client in holomesh/client.py is untouched.

Two-host architecture (probed live 2026-07-16):
- HOLOMESH API   https://mcp.holoscript.net/api/holomesh
  (HoloScript mcp-server, service "holoscript-mcp"; also serves /mcp
  streamable-HTTP MCP. Railway alias returns 401 on these paths — use
  the .net host.)
- KNOWLEDGE STORE https://mcp-orchestrator-production-45f9.up.railway.app
  /knowledge/query + /knowledge/sync (header x-mcp-api-key).
  AUTH DRIFT (W.850): /knowledge/sync in production accepts MCP_API_KEY
  but rejects HOLOSCRIPT_API_KEY, while /knowledge/query answers any key
  with redacted metadata (masking the drift). This client therefore takes
  a *list* of candidate knowledge keys and fails over on 401/403.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "holomesh.async_client requires httpx — "
        "install with `pip install holoscript-mesh[async]`"
    ) from exc

HOLOMESH_BASE = os.environ.get(
    "HOLOMESH_API_URL", "https://mcp.holoscript.net/api/holomesh"
)
KNOWLEDGE_BASE = os.environ.get(
    "HOLOMESH_KNOWLEDGE_URL",
    "https://mcp-orchestrator-production-45f9.up.railway.app",
)

# A signer takes the raw request body and returns the signed envelope dict.
# Wire holomesh.signing.build_signed_envelope via functools.partial, or pass
# None to send unsigned (read routes; grace-period servers).
Signer = Callable[[dict], dict]


class AsyncHoloMesh:
    """Async client for HoloMesh team coordination + the knowledge store.

    Usage:
        async with AsyncHoloMesh(api_key=..., team_id=...) as mesh:
            board = await mesh.board()
            await mesh.heartbeat()
            hits = await mesh.knowledge_query("compression patterns")
    """

    def __init__(
        self,
        api_key: str | None = None,
        team_id: str | None = None,
        base_url: str = HOLOMESH_BASE,
        knowledge_base_url: str = KNOWLEDGE_BASE,
        knowledge_api_keys: list[str] | None = None,
        signer: Optional[Signer] = None,
        timeout: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("HOLOMESH_KEY") or os.environ.get(
            "HOLOMESH_API_KEY", ""
        )
        self.team_id = team_id or os.environ.get("HOLOMESH_TEAM_ID", "")
        self.base_url = base_url.rstrip("/")
        self.knowledge_base_url = knowledge_base_url.rstrip("/")
        # Per-endpoint key selection: try in order, fail over on 401/403.
        self.knowledge_api_keys = knowledge_api_keys or [
            k
            for k in (
                os.environ.get("MCP_API_KEY", ""),
                os.environ.get("HOLOSCRIPT_API_KEY", ""),
            )
            if k
        ]
        self.signer = signer
        self._client = httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> "AsyncHoloMesh":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    # -- low-level ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _get(self, path: str, **params: Any) -> dict:
        r = await self._client.get(
            f"{self.base_url}{path}", headers=self._headers(), params=params or None
        )
        r.raise_for_status()
        return r.json()

    async def _send(self, method: str, path: str, body: dict) -> dict:
        """POST/PATCH with optional Phase 3 signing.

        If a signer is configured the body is wrapped in the signed envelope;
        on 401 signing-rejected without a signer, the error surfaces with a
        pointer to holomesh.signing.
        """
        payload = self.signer(body) if self.signer else body
        r = await self._client.request(
            method, f"{self.base_url}{path}", headers=self._headers(), json=payload
        )
        if r.status_code == 401 and not self.signer:
            detail = r.text[:200]
            raise PermissionError(
                f"{method} {path} -> 401 ({detail}). Mutating HoloMesh routes "
                "require Phase 3 request signing — construct AsyncHoloMesh with "
                "signer=functools.partial(holomesh.signing.build_signed_envelope, "
                "seat_id=...)"
            )
        r.raise_for_status()
        return r.json()

    # -- board ---------------------------------------------------------------

    async def board(self) -> dict:
        """GET /team/:id/board — {tasks: [...]} flat list with status field."""
        return await self._get(f"/team/{self.team_id}/board")

    async def done_log(self, limit: int = 20, offset: int = 0) -> dict:
        return await self._get(
            f"/team/{self.team_id}/board/done", limit=limit, offset=offset
        )

    async def add_task(
        self, title: str, priority: int = 2, tags: list[str] | None = None, **extra: Any
    ) -> dict:
        return await self._send(
            "POST",
            f"/team/{self.team_id}/board",
            {"title": title, "priority": priority, "tags": tags or [], **extra},
        )

    async def claim(self, task_id: str) -> dict:
        return await self._send(
            "PATCH", f"/team/{self.team_id}/board/{task_id}", {"action": "claim"}
        )

    async def done(
        self,
        task_id: str,
        commit: str = "",
        summary: str = "",
        verification_evidence: str = "",
    ) -> dict:
        """Complete a task. Pass EvidenceReceipt.as_verification_evidence() here."""
        return await self._send(
            "PATCH",
            f"/team/{self.team_id}/board/{task_id}",
            {
                "action": "done",
                "commit": commit,
                "summary": summary,
                "verification_evidence": verification_evidence,
            },
        )

    # -- messages / inbox ------------------------------------------------------

    async def messages(self, limit: int = 10) -> dict:
        return await self._get(f"/team/{self.team_id}/messages", limit=limit)

    async def send(self, content: str, msg_type: str = "text", to: str | None = None) -> dict:
        body: dict[str, Any] = {"type": msg_type, "content": content}
        if to:
            body["to"] = to
        return await self._send("POST", f"/team/{self.team_id}/message", body)

    async def inbox(self, unread_only: bool = True, limit: int = 20) -> dict:
        """GET /messages/inbox — agent-scoped (bearer identifies the agent)."""
        return await self._get(
            "/messages/inbox",
            unread_only="true" if unread_only else "false",
            limit=limit,
        )

    # -- presence / suggestions -------------------------------------------------

    async def heartbeat(self, status: str = "active", ide_type: str = "python") -> dict:
        return await self._send(
            "POST",
            f"/team/{self.team_id}/presence",
            {"ide_type": ide_type, "status": status},
        )

    async def presence(self) -> dict:
        return await self._get(f"/team/{self.team_id}/presence")

    async def suggestions(self) -> dict:
        return await self._get(f"/team/{self.team_id}/suggestions")

    async def suggest(self, content: str, **extra: Any) -> dict:
        return await self._send(
            "POST", f"/team/{self.team_id}/suggestions", {"content": content, **extra}
        )

    async def vote_suggestion(self, suggestion_id: str, vote: str = "up") -> dict:
        return await self._send(
            "POST",
            f"/team/{self.team_id}/suggestions/{suggestion_id}/vote",
            {"vote": vote},
        )

    # -- team knowledge (HoloMesh-side) ------------------------------------------

    async def team_knowledge(self, limit: int = 20) -> dict:
        return await self._get(f"/team/{self.team_id}/knowledge", limit=limit)

    async def contribute_team(
        self, entry_type: str, content: str, domain: str = "general", **extra: Any
    ) -> dict:
        entry = {"type": entry_type, "content": content, "domain": domain, **extra}
        return await self._send(
            "POST", f"/team/{self.team_id}/knowledge", {"entries": [entry]}
        )

    # -- knowledge store (orchestrator-side, x-mcp-api-key) -----------------------

    async def _knowledge_post(self, path: str, body: dict) -> dict:
        """POST to the orchestrator knowledge store, failing over across keys.

        Handles the W.850 auth drift: tries each candidate key until one is
        accepted; raises with all statuses if none work.
        """
        if not self.knowledge_api_keys:
            raise PermissionError(
                "no knowledge-store API key configured — set MCP_API_KEY "
                "(preferred) or HOLOSCRIPT_API_KEY, or pass knowledge_api_keys=[...]"
            )
        attempts: list[str] = []
        for key in self.knowledge_api_keys:
            r = await self._client.post(
                f"{self.knowledge_base_url}{path}",
                headers={"Content-Type": "application/json", "x-mcp-api-key": key},
                json=body,
            )
            if r.status_code in (401, 403):
                attempts.append(f"key ..{key[-4:]}: {r.status_code}")
                continue
            r.raise_for_status()
            return r.json()
        raise PermissionError(f"all knowledge keys rejected for {path}: {attempts}")

    async def knowledge_query(self, search: str, limit: int = 5) -> dict:
        """POST /knowledge/query — semantic search over the shared store."""
        return await self._knowledge_post(
            "/knowledge/query", {"search": search, "limit": limit}
        )

    async def knowledge_sync(self, entries: list[dict]) -> dict:
        """POST /knowledge/sync — write entries. Requires the agent-role key
        (MCP_API_KEY in production as of 2026-07-16; see module docstring)."""
        return await self._knowledge_post("/knowledge/sync", {"entries": entries})
