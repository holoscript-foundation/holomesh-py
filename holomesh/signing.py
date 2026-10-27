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
import re
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


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _escape_lone_surrogates(encoded: str) -> str:
    """JS JSON.stringify is well-formed: a lone UTF-16 surrogate is emitted as a
    lowercase \\udXXX escape. Python (ensure_ascii=False) would emit the raw code
    point, which cannot be UTF-8 encoded for signing. Python str code points in
    D800-DFFF are always unpaired, so escape every one of them."""
    return _LONE_SURROGATE.sub(lambda m: "\\u%04x" % ord(m.group()), encoded)


def _dumps_scalar(obj: Any) -> str:
    out = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return _escape_lone_surrogates(out) if isinstance(obj, str) else out


def _utf16_key(key: str) -> bytes:
    """JS Array.prototype.sort() with no comparator orders strings by UTF-16 code
    unit; Python orders by code point. They differ when an astral character
    (surrogate pair, D800-DBFF first) meets a BMP character in E000-FFFF. UTF-16-BE
    bytes compare in code-unit order, so they reproduce the JS ordering."""
    if not isinstance(key, str):
        raise TypeError(f"canonicalize_body object keys must be str, got {type(key).__name__}")
    return key.encode("utf-16-be", "surrogatepass")


def canonicalize_body(obj: Any) -> str:
    """Deterministic JSON: sorted keys at every level, no whitespace.

    Must produce byte-identical output to holomesh-signing.mjs
    canonicalizeBody (JS JSON.stringify semantics). Verified against golden
    vectors generated from the real JS (tests/golden/, tests/test_signing_golden.py):
    - ensure_ascii=False (JS leaves non-ASCII literal)
    - separators without spaces
    - keys sorted by UTF-16 code unit like JS, not by code point
    - lone surrogates escaped as \\udXXX like well-formed JSON.stringify
    - CAVEAT (floats): JS JSON.stringify(1.0) === "1" while json.dumps(1.0) == "1.0";
      exponent forms differ too (1e16, 1e-7). Keep signed bodies free of floats
      that are integral or very large/small (use int), or the signature will not
      verify server-side. Integers beyond 2**53 are not representable in JS at all.
      NaN/Infinity: JS signs `null`, Python emits NaN -- never put them in a body.
    """
    if obj is None or not isinstance(obj, (dict, list)):
        return _dumps_scalar(obj)
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize_body(v) for v in obj) + "]"
    keys = sorted(obj.keys(), key=_utf16_key)
    return (
        "{"
        + ",".join(
            _dumps_scalar(k) + ":" + canonicalize_body(obj[k])
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
