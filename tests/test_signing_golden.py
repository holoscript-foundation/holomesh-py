"""Golden-vector tests: holomesh.signing must be byte-identical to
ai-ecosystem hooks/lib/holomesh-signing.mjs.

Vectors in tests/golden/signing_vectors.json were produced by running the real JS
(tests/golden/gen_vectors.mjs, see tests/golden/README.md). Canonicalization tests
need only the stdlib; envelope tests need the `signing` extra (eth-account,
cryptography) and are skipped without it -- install `holoscript-mesh[signing]` to run them.

TEST-ONLY key material is rebuilt here by formula; no real seat secret is stored.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from holomesh.signing import CanonicalizationError, canonicalize_body

VECTORS_PATH = Path(__file__).parent / "golden" / "signing_vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))

TEST_PRIVKEY = "0x" + "00" * 31 + "01"
MASTER_KEY = bytes([0x42]) * 32


FLOAT_VECTORS_NOW_REFUSED = {
    "float-fractional-same",
    "float-exp-1e21-same-format",
    "float-fractional-0.1",
}


def _has_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_has_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_float(v) for v in obj)
    return False


def test_float_vector_names_pinned():
    found = {c["name"] for c in VECTORS["canonicalize"] if _has_float(json.loads(c["input_json"]))}
    assert found == FLOAT_VECTORS_NOW_REFUSED


def _ids(cases):
    return [c["name"] for c in cases]


def test_vector_file_shape():
    assert VECTORS["schema"] == "holomesh-py.signing-golden/v1"
    assert VECTORS["source"]["file"] == "hooks/lib/holomesh-signing.mjs"
    assert len(VECTORS["source"]["sha256"]) == 64
    # The categories the merge gate requires must all be present.
    names = {c["name"] for c in VECTORS["canonicalize"]}
    for required in (
        "unicode-emoji-astral",
        "unicode-cjk",
        "key-order-nested",
        "key-order-case-and-digits",
        "int-max-safe",
        "signed-payload-shape",
    ):
        assert required in names


@pytest.mark.parametrize("case", VECTORS["canonicalize"], ids=_ids(VECTORS["canonicalize"]))
def test_canonicalize_matches_js(case):
    obj = json.loads(case["input_json"])
    if _has_float(obj):
        # Fail-closed: Python refuses every float (its formatting is not JS's), even the
        # ones whose bytes happen to match. Pinned by name so the list cannot drift.
        assert case["name"] in FLOAT_VECTORS_NOW_REFUSED
        with pytest.raises(CanonicalizationError):
            canonicalize_body(obj)
        return
    got = canonicalize_body(obj)
    assert got == case["expected"]
    # Byte-level too: this is what actually gets signed (UTF-8 of the string).
    assert got.encode("utf-8") == case["expected"].encode("utf-8")


@pytest.mark.parametrize(
    "case",
    VECTORS["canonicalize_float_divergences"],
    ids=_ids(VECTORS["canonicalize_float_divergences"]),
)
def test_float_divergences_are_refused(case):
    """Python floats are NOT byte-identical to JS numbers (JS 1.0 -> "1", Python 1.0 ->
    "1.0"; exponent formats differ). These used to sign silently-different bytes; now
    canonicalize_body refuses every float (fail closed). Send ints instead.
    """
    obj = json.loads(case["input_json"])
    with pytest.raises(CanonicalizationError):
        canonicalize_body(obj)


def test_int_equivalent_of_integral_float_matches_js():
    # The documented workaround: send 1, not 1.0.
    js = {c["name"]: c["expected"] for c in VECTORS["canonicalize_float_divergences"]}
    assert canonicalize_body({"a": 1}) == js["float-integral-1.0"]
    assert canonicalize_body({"a": 100}) == js["float-integral-100.0"]


def test_non_str_keys_fail_closed():
    # JS object keys are always strings; a Python int key would serialize unquoted
    # and could never verify. Refuse rather than sign something the server rejects.
    with pytest.raises(TypeError):
        canonicalize_body({1: "a"})

# ---- envelope / signature vectors (need the `signing` extra) ----


@pytest.fixture(scope="module")
def seats_dir(tmp_path_factory):
    pytest.importorskip("eth_account")
    pytest.importorskip("cryptography")
    root = tmp_path_factory.mktemp("seats")
    tk = VECTORS["test_key"]
    seat = root / tk["seat_id"]
    seat.mkdir()
    (seat / "wallet.enc").write_text(json.dumps(tk["wallet_enc"]), encoding="utf-8")
    (root / ".master-key").write_bytes(MASTER_KEY)
    return root


def test_wallet_decrypts_js_aes_gcm_blob(seats_dir):
    from holomesh.signing import load_seat_wallet

    wallet = load_seat_wallet(VECTORS["test_key"]["seat_id"], seats_dir)
    assert wallet["private_key"] == TEST_PRIVKEY
    assert wallet["address"] == VECTORS["test_key"]["wallet_enc"]["address"]


@pytest.mark.parametrize("case", VECTORS["envelopes"], ids=_ids(VECTORS["envelopes"]))
def test_signed_envelope_matches_js(case, seats_dir):
    from holomesh.signing import build_signed_envelope

    body = json.loads(case["body_json"])
    got = build_signed_envelope(
        body,
        VECTORS["test_key"]["seat_id"],
        seats_dir=seats_dir,
        nonce=case["nonce"],
        now_ms=case["now_ms"],
    )
    want = case["envelope"]
    assert got["timestamp"] == want["timestamp"]
    assert got["nonce"] == want["nonce"]
    assert got["signer_address"] == want["signer_address"]
    assert got["signature"] == want["signature"]
    assert got == want


def test_signature_recovers_to_signer(seats_dir):
    """Independent check: the Python signature verifies against the canonical payload
    exactly as a server doing EIP-191 recovery would."""
    from eth_account import Account
    from eth_account.messages import encode_defunct

    case = VECTORS["envelopes"][0]
    env = case["envelope"]
    payload = canonicalize_body(
        {"body": env["body"], "nonce": env["nonce"], "timestamp": env["timestamp"]}
    )
    recovered = Account.recover_message(encode_defunct(text=payload), signature=env["signature"])
    assert recovered.lower() == env["signer_address"].lower()


def test_aes_blob_is_base64():
    enc = VECTORS["test_key"]["wallet_enc"]["encrypted_privkey"]
    for field in ("iv", "ct", "tag"):
        base64.b64decode(enc[field], validate=True)