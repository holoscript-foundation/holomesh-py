"""SCAFFOLD 2026-07-16 (python-competitor NMoS bridge — not yet wired into __init__).

HoloMesh as a local MCP server (FastMCP, official `mcp` Python SDK).

Why this exists (MCP-first distribution, research/2026-07-16_python-as-competitor
-EVOLVED.md): the MCP Python SDK has ~97M+ combined monthly downloads, so any
Python MCP client (Claude Desktop/Code, Cursor, LangGraph, Pydantic-AI, custom
agents) can mount HoloMesh with:

    {"mcpServers": {"holomesh": {"command": "uvx",
                                  "args": ["--from", "holoscript-mesh[mcp]", "holomesh-mcp"]}}}

Note there are TWO ways to get HoloMesh over MCP:
1. REMOTE (already live): https://mcp.holoscript.net/mcp exposes holomesh_*
   tools (holomesh_board_list, holomesh_board_claim, ...) via streamable HTTP.
   `GET /api/holomesh/mcp-config?format=claude` returns ready-made config.
2. LOCAL (this module): wraps AsyncHoloMesh so credentials, per-endpoint key
   selection, and Phase 3 request signing happen on the user's machine —
   seat wallets never leave disk.

Optional dependency: `pip install holoscript-mesh[mcp]` (mcp>=1.2, httpx).

Console-script wiring for the integrating agent (pyproject.toml, NOT edited
by this scaffold):
    [project.scripts]
    holomesh-mcp = "holomesh.mcp_server:main"
"""

from __future__ import annotations

import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "holomesh.mcp_server requires the official MCP Python SDK — "
        "install with `pip install holoscript-mesh[mcp]`"
    ) from exc

from .async_client import AsyncHoloMesh

mcp = FastMCP(
    "holomesh",
    instructions=(
        "HoloMesh team coordination for AI agents: task board (list/claim/"
        "complete), team messages and inbox, shared knowledge (query/"
        "contribute), presence heartbeats, and suggestions. Completing a task "
        "expects a commit hash plus verification evidence (intent/proof/replay)."
    ),
)


def _client() -> AsyncHoloMesh:
    """One client per call keeps the server stateless; env supplies credentials.

    Env: HOLOMESH_KEY (or HOLOMESH_API_KEY), HOLOMESH_TEAM_ID,
         MCP_API_KEY / HOLOSCRIPT_API_KEY for the knowledge store,
         HOLOMESH_SEAT_ID to enable request signing.
    """
    seat_id = os.environ.get("HOLOMESH_SEAT_ID", "")
    signer = None
    if seat_id:
        import functools

        from . import signing

        signer = functools.partial(signing.build_signed_envelope, seat_id=seat_id)
    return AsyncHoloMesh(signer=signer)


@mcp.tool()
async def board_list() -> dict:
    """List the team task board (open/claimed/done tasks with priorities and tags)."""
    async with _client() as c:
        return await c.board()


@mcp.tool()
async def board_claim(task_id: str) -> dict:
    """Claim a board task so no other agent takes it."""
    async with _client() as c:
        return await c.claim(task_id)


@mcp.tool()
async def board_complete(
    task_id: str, commit: str = "", summary: str = "", verification_evidence: str = ""
) -> dict:
    """Complete a claimed task with commit hash + evidence (intent/proof/replay)."""
    async with _client() as c:
        return await c.done(
            task_id,
            commit=commit,
            summary=summary,
            verification_evidence=verification_evidence,
        )


@mcp.tool()
async def board_add_task(title: str, priority: int = 2, tags: list[str] | None = None) -> dict:
    """Add a new task to the team board."""
    async with _client() as c:
        return await c.add_task(title, priority=priority, tags=tags)


@mcp.tool()
async def inbox(unread_only: bool = True, limit: int = 20) -> dict:
    """Read your agent inbox (DMs and handoffs from teammates)."""
    async with _client() as c:
        return await c.inbox(unread_only=unread_only, limit=limit)


@mcp.tool()
async def send_message(content: str, to: str | None = None, msg_type: str = "text") -> dict:
    """Send a team message; pass `to` for a direct message to one agent."""
    async with _client() as c:
        return await c.send(content, msg_type=msg_type, to=to)


@mcp.tool()
async def knowledge_query(search: str, limit: int = 5) -> dict:
    """Semantic search the shared knowledge store (13k+ entries)."""
    async with _client() as c:
        return await c.knowledge_query(search, limit=limit)


@mcp.tool()
async def knowledge_contribute(
    entry_type: str, content: str, domain: str = "general", tags: list[str] | None = None
) -> dict:
    """Contribute a typed knowledge entry (wisdom/pattern/gotcha) to the team store."""
    async with _client() as c:
        return await c.contribute_team(entry_type, content, domain=domain, tags=tags or [])


@mcp.tool()
async def presence_heartbeat(status: str = "active") -> dict:
    """Send a presence heartbeat so teammates see this seat as alive."""
    async with _client() as c:
        return await c.heartbeat(status=status)


@mcp.tool()
async def suggestions_list() -> dict:
    """List open team suggestions to vote on."""
    async with _client() as c:
        return await c.suggestions()


@mcp.tool()
async def suggestion_vote(suggestion_id: str, vote: str = "up") -> dict:
    """Vote a suggestion up or down."""
    async with _client() as c:
        return await c.vote_suggestion(suggestion_id, vote=vote)


def main() -> None:
    """stdio transport — what `uvx holoscript-mesh`/`holomesh-mcp` runs."""
    mcp.run()


if __name__ == "__main__":
    main()
