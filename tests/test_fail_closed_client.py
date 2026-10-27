"""Fail-closed client tests: async_client._send and mcp_server._client.

Needs httpx (the `async` extra); `mcp` is stubbed when not installed.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import threading
import types

import pytest

httpx = pytest.importorskip("httpx")

from holomesh import signing  # noqa: E402
from holomesh.async_client import AsyncHoloMesh  # noqa: E402
from holomesh.signing import CanonicalizationError, SigningError  # noqa: E402


def _fake_seat(tmp_path, seat="seat-a"):
    (tmp_path / seat).mkdir()
    (tmp_path / seat / "wallet.enc").write_text("{}", encoding="utf-8")
    (tmp_path / ".master-key").write_bytes(b"\x00" * 32)
    return seat


def _client_with(handler, **kw):
    c = AsyncHoloMesh(api_key="k", team_id="t", base_url="https://example.invalid", **kw)
    asyncio.run(c._client.aclose())
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def _recording_handler():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    return sent, handler


def _run(c, body):
    async def go():
        try:
            return await c._send("POST", "/x", body)
        finally:
            await c._client.aclose()

    return asyncio.run(go())


def _good_env(body):
    return {"body": body, "signature": "0x" + "ab" * 65, "signer_address": "0x" + "11" * 20,
            "nonce": "n", "timestamp": "2026-01-01T00:00:00.000Z"}


def test_send_without_signer_refuses(monkeypatch):
    monkeypatch.delenv("HOLOMESH_ALLOW_UNSIGNED", raising=False)
    sent, handler = _recording_handler()
    c = _client_with(handler)
    with pytest.raises(SigningError, match="HOLOMESH_ALLOW_UNSIGNED"):
        _run(c, {"a": 1})
    assert sent == []


def test_send_unsigned_opt_in_warns_every_build(monkeypatch, caplog):
    monkeypatch.setenv("HOLOMESH_ALLOW_UNSIGNED", "1")
    caplog.set_level(logging.WARNING, logger="holomesh")
    sent, handler = _recording_handler()
    _client_with(handler)
    c = _client_with(handler)
    warnings = [r for r in caplog.records if "UNSIGNED" in r.getMessage()]
    assert len(warnings) == 2
    assert _run(c, {"a": 1}) == {"ok": True}
    assert sent == [{"a": 1}]


def test_send_explicit_allow_unsigned_kwarg(monkeypatch):
    monkeypatch.delenv("HOLOMESH_ALLOW_UNSIGNED", raising=False)
    sent, handler = _recording_handler()
    c = _client_with(handler, allow_unsigned=True)
    _run(c, {"a": 1})
    assert sent == [{"a": 1}]


@pytest.mark.parametrize("exc", [ValueError("boom"), CanonicalizationError("float"), FileNotFoundError("x")])
def test_signer_error_is_wrapped_and_nothing_sent(exc):
    sent, handler = _recording_handler()

    def signer(body):
        raise exc

    c = _client_with(handler, signer=signer)
    with pytest.raises(SigningError) as info:
        _run(c, {"a": 1})
    assert info.value.__cause__ is exc
    assert type(exc).__name__ in str(info.value)
    assert sent == []


@pytest.mark.parametrize(
    "bad",
    [None, [], {"body": {"a": 1}}, dict(_good_env({"a": 2})),
     dict(_good_env({"a": 1}), signature="ab"), dict(_good_env({"a": 1}), signer_address="0x1"),
     dict(_good_env({"a": 1}), nonce=5)],
)
def test_bad_envelope_refused_and_nothing_sent(bad):
    sent, handler = _recording_handler()
    c = _client_with(handler, signer=lambda body: bad)
    with pytest.raises(SigningError):
        _run(c, {"a": 1})
    assert sent == []


def test_good_signer_runs_off_loop_thread_and_sends_envelope():
    sent, handler = _recording_handler()
    seen = {}
    loop_thread = threading.get_ident()

    def signer(body):
        seen["thread"] = threading.get_ident()
        return _good_env(body)

    c = _client_with(handler, signer=signer)
    _run(c, {"a": 1})
    assert sent == [_good_env({"a": 1})]
    assert seen["thread"] != loop_thread


# -- mcp_server._client ------------------------------------------------------------


@pytest.fixture
def mcp_server(monkeypatch):
    try:
        importlib.import_module("mcp.server.fastmcp")
    except ImportError:
        class FastMCP:
            def __init__(self, *a, **k):
                pass

            def tool(self, *a, **k):
                return lambda f: f

            def run(self, *a, **k):
                pass

        for name in ("mcp", "mcp.server", "mcp.server.fastmcp"):
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        sys.modules["mcp.server.fastmcp"].FastMCP = FastMCP
    monkeypatch.delitem(sys.modules, "holomesh.mcp_server", raising=False)
    return importlib.import_module("holomesh.mcp_server")


def _close(c):
    asyncio.run(c._client.aclose())


def test_mcp_client_refuses_without_seat(mcp_server, monkeypatch):
    monkeypatch.delenv("HOLOMESH_SEAT_ID", raising=False)
    monkeypatch.delenv("HOLOMESH_ALLOW_UNSIGNED", raising=False)
    with pytest.raises(SigningError):
        mcp_server._client()


def test_mcp_client_unsigned_opt_in_warns(mcp_server, monkeypatch, caplog):
    monkeypatch.delenv("HOLOMESH_SEAT_ID", raising=False)
    monkeypatch.setenv("HOLOMESH_ALLOW_UNSIGNED", "1")
    caplog.set_level(logging.WARNING, logger="holomesh")
    c1 = mcp_server._client()
    c2 = mcp_server._client()
    assert c1.signer is None and c2.signer is None
    assert len([r for r in caplog.records if "UNSIGNED" in r.getMessage()]) == 2
    _close(c1)
    _close(c2)


def test_mcp_client_with_seat_signs(mcp_server, monkeypatch, tmp_path):
    pytest.importorskip("eth_account")
    pytest.importorskip("cryptography")
    seat = _fake_seat(tmp_path)
    monkeypatch.setattr(signing, "SEATS_DIR", tmp_path)
    monkeypatch.setenv("HOLOMESH_SEAT_ID", seat)
    c = mcp_server._client()
    assert callable(c.signer)
    _close(c)