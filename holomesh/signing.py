"""SCAFFOLD 2026-07-16 (python-competitor NMoS bridge — not yet wired into __init__).

Phase 3 HoloMesh request signing — Python port of
ai-ecosystem/hooks/lib/holomesh-signing.mjs.

Mutating HoloMesh routes (board POST/PATCH, message POST, presence POST, ...)
reject unsigned bodies with 401 {"error": "signing-rejected"}. The accepted
envelope is:

    {body, signature, signer_address, nonce, timestamp}

where `signature` is an EIP-191 personal_sign over
`canonicalize_body({"body": body, "nonce": nonce, "timestamp": timestamp})`
and the signer is the seat's secp256k1 wallet.

Optional dependencies (install with `pip install holoscript-mesh[signing]`):
    eth-account>=0.10   (EIP-191 personal_sign)
    cryptography>=42    (AES-256-GCM wallet.enc decryption)

Dependency-sovereignty note (D.128): crypto is silent-adversarial tier —
WRAP forever, never rebuild. eth-account/cryptography are the wrap.

Seat wallets live at ~/.ai-ecosystem/seats/<seat_id>/wallet.enc encrypted
with ~/.ai-ecosystem/seats/.master-key (AES-256-GCM). Seat id convention:
`claude-<surface>-<fp8>-default-x402` with fp8 = sha256(hostname+homedir)[:8].
External (non-fleet) users won't have seats — they use read routes plus the
hosted MCP tools instead; see README.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

SEATS_DIR = Path(
    os.environ.get("HOLOMESH_SEATS_DIR_OVERRIDE")
    or Path.home() / ".ai-ecosystem" / "seats"
)


def machine_fingerprint() -> str:
    """sha256(hostname + homedir)[:8] — must match provision-seat-wallets-x402.mjs."""
    raw = socket.gethostname() + str(Path.home())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def canonicalize_body(obj: Any) -> str:
    """Deterministic JSON: sorted keys at every level, no whitespace.

    Must produce byte-identical output to holomesh-signing.mjs
    canonicalizeBody (JS JSON.stringify semantics):
    - ensure_ascii=False (JS leaves non-ASCII literal)
    - separators without spaces
    - CAVEAT: JS JSON.stringify(1.0) === "1" while json.dumps(1.0) == "1.0".
      Keep signed bodies free of float-typed integers (use int), or the
      signature will not verify server-side.
    """
    if obj is None or not isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize_body(v) for v in obj) + "]"
    keys = sorted(obj.keys())
    return (
        "{"
        + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + canonicalize_body(obj[k])
            for k in keys
        )
        + "}"
    )


def make_nonce() -> str:
    return os.urandom(16).hex()


def load_seat_wallet(seat_id: str, seats_dir: Path = SEATS_DIR) -> dict:
    """Decrypt <seats_dir>/<seat_id>/wallet.enc -> {"address", "private_key"}."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "seat wallet decryption requires `cryptography` — "
            "install with `pip install holoscript-mesh[signing]`"
        ) from exc

    wallet_path = seats_dir / seat_id / "wallet.enc"
    master_key_path = seats_dir / ".master-key"
    if not wallet_path.exists():
        raise FileNotFoundError(f"wallet.enc not found at {wallet_path}")
    if not master_key_path.exists():
        raise FileNotFoundError(f"master-key not found at {master_key_path}")

    blob = json.loads(wallet_path.read_text())
    enc = blob["encrypted_privkey"]
    master_key = master_key_path.read_bytes()
    iv = base64.b64decode(enc["iv"])
    ct = base64.b64decode(enc["ct"])
    tag = base64.b64decode(enc["tag"])
    # node crypto splits ct/tag; AESGCM.decrypt expects ct||tag concatenated.
    plaintext = AESGCM(master_key).decrypt(iv, ct + tag, None)
    return {"address": blob["address"], "private_key": plaintext.decode("utf-8")}


def build_signed_envelope(
    body: dict,
    seat_id: str,
    seats_dir: Path = SEATS_DIR,
    nonce: str | None = None,
    now_ms: int | None = None,
) -> dict:
    """Wrap `body` in the signed envelope the Phase 3 server verifies.

    Returns {"body", "signature", "signer_address", "nonce", "timestamp"}.
    Timestamp freshness window server-side is 5 minutes.
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "request signing requires `eth-account` — "
            "install with `pip install holoscript-mesh[signing]`"
        ) from exc

    wallet = load_seat_wallet(seat_id, seats_dir)
    nonce = nonce or make_nonce()
    ts_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    # ISO-8601 with milliseconds + Z, matching JS `new Date().toISOString()`.
    timestamp = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms / 1000))
        + f".{ts_ms % 1000:03d}Z"
    )
    payload = canonicalize_body({"body": body, "nonce": nonce, "timestamp": timestamp})
    signed = Account.sign_message(
        encode_defunct(text=payload), private_key=wallet["private_key"]
    )
    return {
        "body": body,
        "signature": signed.signature.to_0x_hex()
        if hasattr(signed.signature, "to_0x_hex")
        else "0x" + signed.signature.hex().removeprefix("0x"),
        "signer_address": wallet["address"],
        "nonce": nonce,
        "timestamp": timestamp,
    }
