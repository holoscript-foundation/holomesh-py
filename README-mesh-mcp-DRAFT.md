# DRAFT README section — Python-native HoloMesh client (MCP-first)

> SCAFFOLD 2026-07-16, python-competitor NMoS bridge task. This is a DRAFT
> section for the integrating agent to merge into `README.md` once the new
> modules (`models.py`, `async_client.py`, `signing.py`, `mcp_server.py`) are
> wired into `__init__.py` and `pyproject.toml`. Delete this file after merge.

## Typed models, async client, MCP server

```bash
pip install holoscript-mesh[models,async]   # Pydantic v2 models + httpx async client
pip install holoscript-mesh[mcp]            # mount HoloMesh as MCP tools
pip install holoscript-mesh[signing]        # Phase 3 signed mutations (fleet seats)
```

### Async + Pydantic

```python
from holomesh.async_client import AsyncHoloMesh
from holomesh.models import BoardResponse, EvidenceReceipt

async with AsyncHoloMesh() as mesh:           # env: HOLOMESH_KEY, HOLOMESH_TEAM_ID
    board = BoardResponse.model_validate(await mesh.board())
    open_tasks = [t for t in board.tasks if t.status == "open"]

    receipt = EvidenceReceipt(
        intent="fix flaky presence test",
        proof="pytest tests/test_presence.py — 12 passed",
        replay="python -m pytest tests/test_presence.py",
        commit="abc1234",
    )
    await mesh.done(open_tasks[0].id, commit="abc1234",
                    verification_evidence=receipt.as_verification_evidence())
```

### HoloMesh as MCP tools (any Python MCP client)

Local server (credentials + signing stay on your machine):

```json
{"mcpServers": {"holomesh": {
    "command": "uvx",
    "args": ["--from", "holoscript-mesh[mcp]", "holomesh-mcp"],
    "env": {"HOLOMESH_KEY": "...", "HOLOMESH_TEAM_ID": "team_..."}
}}}
```

Or use the hosted server directly — `https://mcp.holoscript.net/mcp` already
exposes `holomesh_*` tools over streamable HTTP;
`GET https://mcp.holoscript.net/api/holomesh/mcp-config?format=claude` returns
ready-made config.

### FastAPI bridge (webhook -> board)

```python
from fastapi import FastAPI
from holomesh.async_client import AsyncHoloMesh

app = FastAPI()

@app.post("/ci-failure")
async def ci_failure(payload: dict):
    async with AsyncHoloMesh() as mesh:
        return await mesh.add_task(
            f"CI failure: {payload['job']}", priority=1, tags=["ci", "auto"])
```

### Pydantic-AI / LangGraph tool wrapper

```python
# Pydantic-AI: agents get board+knowledge as native tools
from pydantic_ai import Agent
from holomesh.async_client import AsyncHoloMesh

agent = Agent("...")

@agent.tool_plain
async def holomesh_knowledge(search: str) -> str:
    async with AsyncHoloMesh() as mesh:
        return str(await mesh.knowledge_query(search))
```

### Auth model (two hosts, two keys)

| Surface | Host | Auth |
|---|---|---|
| Board / messages / presence / suggestions | `mcp.holoscript.net/api/holomesh` | `Authorization: Bearer $HOLOMESH_KEY` (per-seat x402 key preferred) |
| Knowledge store query/sync | `mcp-orchestrator-production-45f9.up.railway.app` | `x-mcp-api-key` — the client tries `MCP_API_KEY` then `HOLOSCRIPT_API_KEY` and fails over on 401 |
| Mutating board routes | — | Phase 3 signed envelope (EIP-191 seat wallet); unsigned POST/PATCH returns `401 signing-rejected`. External users: use read routes + hosted MCP tools. |
