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
Seat directory: the JS side resolves `<ai-ecosystem root>/seats` (AI_ECOSYSTEM_DIR /
AI_ECOSYSTEM_ROOT, else derived from its own location); this port defaults to
`~/.ai-ecosystem/seats` (fine where that path is the ecosystem root or a junction to
it) and honors only HOLOMESH_SEATS_DIR_OVERRIDE, so set that on machines where they differ.

Byte-identity with the JS canonicalization and signed envelope is pinned by golden
vectors (tests/golden/, tests/test_signing_golden.py). Fail-closed: anything whose
JS bytes could differ (every float, ints beyond +-(2**53-1), tuples and other
non-JSON types, adjacent surrogate pairs) raises CanonicalizationError instead of
being signed (see canonicalize_body).

External (non-fleet) users won't have seats — they use read routes plus the
hosted MCP tools instead; see README.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import json
import math
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# Largest integer JS represents exactly (Number.MAX_SAFE_INTEGER). Same bound as the
# guards in ai-ecosystem holomesh-signing.mjs and HoloScript request-signing.ts.
MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalizationError(TypeError, ValueError):
    """A body value cannot be canonicalized byte-identically to JS; refuse to sign it."""


class SigningError(RuntimeError):
    """Signing could not be performed safely; nothing was (or may be) sent."""

SEATS_DIR = Path(
    os.environ.get("HOLOMESH_SEATS_DIR_OVERRIDE")
    or Path.home() / ".ai-ecosystem" / "seats"
)


def machine_fingerprint() -> str:
    """sha256(hostname + homedir)[:8] — must match provision-seat-wallets-x402.mjs."""
    raw = socket.gethostname() + str(Path.home())
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_SURROGATE_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")


def _escape_lone_surrogates(encoded: str) -> str:
    """JS JSON.stringify is well-formed: a lone UTF-16 surrogate is emitted as a
    lowercase \\udXXX escape. Python (ensure_ascii=False) would emit the raw code
    point, which cannot be UTF-8 encoded for signing. canonicalize_body has already
    refused adjacent high+low surrogate pairs (JS would read them as one astral
    character), so every D800-DFFF code point left here is unpaired; escape each."""
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
        raise CanonicalizationError(
            f"canonicalize_body object keys must be str, got {type(key).__name__}"
        )
    return key.encode("utf-16-be", "surrogatepass")


def _check_str(value: str, where: str) -> None:
    if _SURROGATE_PAIR.search(value):
        raise CanonicalizationError(
            f"{where} contains an adjacent UTF-16 surrogate pair (a high D800-DBFF code point "
            "followed by a low DC00-DFFF one). JS reads that as one astral character, so the "
            "signed bytes would differ; use the real character instead"
        )


def canonicalize_body(obj: Any) -> str:
    """Deterministic JSON: sorted keys at every level, no whitespace.

    Byte-identical to holomesh-signing.mjs canonicalizeBody (JS JSON.stringify
    semantics) for every value it accepts. Verified against golden vectors generated
    from the real JS (tests/golden/, tests/test_signing_golden.py):
    - ensure_ascii=False (JS leaves non-ASCII literal)
    - separators without spaces
    - keys sorted by UTF-16 code unit like JS, not by code point
    - lone surrogates escaped as \\udXXX like well-formed JSON.stringify

    Accepted: None, bool, int with |n| <= 2**53-1, str, list, dict (subclasses too)
    with str keys. Everything else raises CanonicalizationError (fail closed):
    - every float, including -0.0, NaN, +-Infinity and 1e-6..1e-4: Python float
      text is not JS number text (1.0 vs 1, 1e16 vs 10000000000000000), and JS signs
      NaN/Infinity as null. Send ints.
    - ints beyond +-(2**53-1): JS cannot represent them, so it signs other bytes.
    - tuple, set, bytes, Decimal, non-dict Mapping, ...: convert to list/dict/str.
    - adjacent surrogate pairs in a str or key (see _check_str).
    """
    if obj is None or isinstance(obj, bool):
        return "null" if obj is None else ("true" if obj else "false")
    if isinstance(obj, int):
        if abs(obj) > MAX_SAFE_INTEGER:
            raise CanonicalizationError(
                "integer beyond +-(2**53-1) (JS Number.MAX_SAFE_INTEGER) cannot be signed "
                "byte-identically; send it as a string"
            )
        return str(int(obj))
    if isinstance(obj, float):
        raise CanonicalizationError(
            f"float {obj!r} refused: Python float text is not JS number text (and JS signs "
            "NaN/Infinity as null); signed bodies must use int or str"
        )
    if isinstance(obj, str):
        _check_str(obj, "string value")
        return _dumps_scalar(obj)
    if isinstance(obj, list):
        return "[" + ",".join(canonicalize_body(v) for v in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj.keys(), key=_utf16_key)
        for k in keys:
            _check_str(k, "object key")
        return (
            "{"
            + ",".join(
                _dumps_scalar(k) + ":" + canonicalize_body(obj[k])
                for k in keys
            )
            + "}"
        )
    raise CanonicalizationError(
        f"{type(obj).__name__} cannot be signed: only None, bool, int, str, list and dict "
        "are accepted (convert tuple/set/bytes/Decimal/Mapping first)"
    )


def make_nonce() -> str:
    return os.urandom(16).hex()


_SEAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SUPPORTED_ALG = "aes-256-gcm"
OFFSET_SCHEMA = "holomesh.clock-offset.v1"


def _validate_seat_id(seat_id: Any) -> str:
    if not isinstance(seat_id, str) or not seat_id.strip():
        raise SigningError("seat id is empty: set HOLOMESH_SEAT_ID to your seat id")
    if not _SEAT_ID_RE.match(seat_id) or ".." in seat_id:
        raise SigningError(
            "seat id must be a plain directory name (letters, digits, '.', '_', '-'; "
            "no path separators or '..')"
        )
    return seat_id


def preflight_seat(seat_id: str, seats_dir: Optional[Path] = None) -> None:
    """Check the seat id, the signing extras and that wallet files exist.

    Does not read or decrypt anything, so it is safe to call when a client is built.
    """
    _validate_seat_id(seat_id)
    seats_dir = Path(SEATS_DIR if seats_dir is None else seats_dir)
    try:
        import cryptography.hazmat.primitives.ciphers.aead  # noqa: F401
        import eth_account  # noqa: F401
    except ImportError as exc:
        raise SigningError(
            "request signing requires the `signing` extra: "
            "pip install holoscript-mesh[signing]"
        ) from exc
    if not (seats_dir / seat_id / "wallet.enc").is_file():
        raise SigningError(f"wallet.enc not found at {seats_dir / seat_id / 'wallet.enc'}")
    if not (seats_dir / ".master-key").is_file():
        raise SigningError(f"master-key not found at {seats_dir / '.master-key'}")


def _js_number(raw: str) -> Optional[float]:
    """Subset of JS Number(raw) for an env offset: decimal or 0x/0o/0b; None if not finite."""
    text = raw.strip()
    if "_" in text:
        return None
    try:
        if text[:2].lower() in ("0x", "0o", "0b"):
            n = float(int(text, 0))
        else:
            n = float(text) if text else 0.0
    except ValueError:
        return None
    return n if math.isfinite(n) else None


def _offset_file(env: Mapping[str, str]) -> Path:
    override = env.get("HOLOMESH_CLOCK_OFFSET_FILE")
    if override:
        return Path(override)
    return Path.home() / ".ai-ecosystem" / "runtime" / "shared" / "holomesh-clock-offset.json"


def signing_clock_offset_ms(env: Optional[Mapping[str, str]] = None) -> float:
    """Port of holomesh-clock.mjs getSigningClockOffsetMs. Never raises; 0 if unknown.

    1. env HOLOMESH_SIGNING_CLOCK_OFFSET_MS (finite number), else
    2. the cache file (HOLOMESH_CLOCK_OFFSET_FILE, default
       ~/.ai-ecosystem/runtime/shared/holomesh-clock-offset.json) with schema
       holomesh.clock-offset.v1 and a finite offsetMs.
    """
    env = os.environ if env is None else env
    raw = env.get("HOLOMESH_SIGNING_CLOCK_OFFSET_MS")
    if raw is not None and raw != "":
        n = _js_number(raw)
        if n is not None:
            return n
    try:
        rec = json.loads(_offset_file(env).read_text(encoding="utf-8"))
        off = rec.get("offsetMs") if isinstance(rec, dict) else None
        if (
            rec.get("schema") == OFFSET_SCHEMA
            and isinstance(off, (int, float))
            and not isinstance(off, bool)
            and math.isfinite(off)
        ):
            return float(off)
    except Exception:  # missing/corrupt cache -> no offset
        pass
    return 0.0


def signing_now_ms(env: Optional[Mapping[str, str]] = None) -> int:
    """Date.now() + offset, truncated like JS new Date(x)."""
    return int(int(time.time() * 1000) + signing_clock_offset_ms(env))


def _iso_ms(ts_ms: int) -> str:
    """ISO-8601 with milliseconds + Z, matching JS `new Date(ts).toISOString()`."""
    return (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms // 1000))
        + f".{ts_ms % 1000:03d}Z"
    )


def load_seat_wallet(seat_id: str, seats_dir: Optional[Path] = None) -> dict:
    """Decrypt <seats_dir>/<seat_id>/wallet.enc -> {"address", "private_key"}."""
    _validate_seat_id(seat_id)
    seats_dir = Path(SEATS_DIR if seats_dir is None else seats_dir)
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
    alg = enc.get("alg")
    alg = _SUPPORTED_ALG if alg is None else alg
    if not isinstance(alg, str) or alg.lower() != _SUPPORTED_ALG:
        raise SigningError(f"wallet.enc alg {alg!r} is not supported (only {_SUPPORTED_ALG})")
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
    seats_dir: Optional[Path] = None,
    nonce: str | None = None,
    now_ms: int | None = None,
) -> dict:
    """Wrap `body` in the signed envelope the Phase 3 server verifies.

    Returns {"body", "signature", "signer_address", "nonce", "timestamp"}.
    Timestamp freshness window server-side is 5 minutes. `now_ms` pins the
    timestamp exactly; otherwise the clock offset (signing_clock_offset_ms) applies.
    Refuses (CanonicalizationError / SigningError) before touching the wallet when
    the body or seat id is unusable.
    """
    canonicalize_body(body)
    _validate_seat_id(seat_id)
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "request signing requires `eth-account` — "
            "install with `pip install holoscript-mesh[signing]`"
        ) from exc

    wallet = load_seat_wallet(seat_id, seats_dir)
    nonce = make_nonce() if nonce is None else nonce  # JS: opts.nonce ?? makeNonce()
    ts_ms = int(now_ms) if now_ms is not None else signing_now_ms()
    timestamp = _iso_ms(ts_ms)
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


def signer_from_env(
    env: Optional[Mapping[str, str]] = None, seats_dir: Optional[Path] = None
) -> Optional[Callable[[dict], dict]]:
    """Fail-closed signer policy (used by mcp_server._client).

    - HOLOMESH_SEAT_ID set: preflight the seat and return a signer.
    - unset and HOLOMESH_ALLOW_UNSIGNED == "1" (exactly): return None; the client
      then sends unsigned and logs a warning each time it is built.
    - otherwise: raise SigningError.
    """
    env = os.environ if env is None else env
    seat_id = (env.get("HOLOMESH_SEAT_ID") or "").strip()
    if seat_id:
        seats = Path(SEATS_DIR if seats_dir is None else seats_dir)
        preflight_seat(seat_id, seats)
        return functools.partial(build_signed_envelope, seat_id=seat_id, seats_dir=seats)
    if env.get("HOLOMESH_ALLOW_UNSIGNED") == "1":
        return None
    raise SigningError(
        "HOLOMESH_SEAT_ID is not set: refusing to build an unsigned HoloMesh client. "
        "Set HOLOMESH_SEAT_ID (wallet under HOLOMESH_SEATS_DIR_OVERRIDE or "
        "~/.ai-ecosystem/seats), or set HOLOMESH_ALLOW_UNSIGNED=1 to send unsigned "
        "requests on purpose (read routes / grace-mode servers only)."
    )
