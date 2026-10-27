"""Fail-closed signing tests (card: holomesh-py signer fail-closed).

Rule shared with ai-ecosystem holomesh-signing.mjs and HoloScript request-signing.ts:
a value whose JS JSON.stringify bytes differ from what the caller meant (NaN,
Infinity, integers beyond 2**53-1) must be refused, never signed. Python is
stricter: it refuses every float, because Python float formatting is not
byte-identical to JS. Canonicalization tests need only the stdlib.
"""

from __future__ import annotations

import collections
import decimal
import json
import types

import pytest

from holomesh import signing
from holomesh.signing import (
    MAX_SAFE_INTEGER,
    CanonicalizationError,
    SigningError,
    canonicalize_body,
)

# -- section 1 refusals ------------------------------------------------------------


def test_error_types():
    assert issubclass(CanonicalizationError, TypeError)
    assert issubclass(CanonicalizationError, ValueError)
    assert issubclass(SigningError, RuntimeError)
    assert MAX_SAFE_INTEGER == 2**53 - 1


@pytest.mark.parametrize(
    "value",
    [0.5, 1.0, 0.0, -0.0, float("nan"), float("inf"), float("-inf"),
     1e-6, 5e-5, 1e-4, 1e-7, 1e16, 1e21, 1e308],
    ids=repr,
)
def test_any_float_refused(value):
    with pytest.raises(CanonicalizationError, match="float"):
        canonicalize_body({"a": value})
    with pytest.raises(CanonicalizationError):
        canonicalize_body([1, [value]])
    with pytest.raises(CanonicalizationError):
        canonicalize_body(value)


@pytest.mark.parametrize("value", [2**53, 2**53 + 1, -(2**53), -(2**53) - 1, 10**30, -(10**400)])
def test_int_beyond_safe_range_refused(value):
    with pytest.raises(CanonicalizationError, match="2\\*\\*53"):
        canonicalize_body({"a": value})


@pytest.mark.parametrize("value", [0, 1, -1, MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER])
def test_safe_ints_unchanged(value):
    assert canonicalize_body({"a": value}) == '{"a":%d}' % value


def test_null_bool_str_unchanged():
    assert canonicalize_body({"a": None}) == '{"a":null}'
    assert canonicalize_body({"t": True, "f": False}) == '{"f":false,"t":true}'
    assert canonicalize_body({"s": "x\u00e9"}) == '{"s":"x\u00e9"}'


@pytest.mark.parametrize(
    "value",
    [(), (1, 2), ({"a": 1},), ({"a": 1}, {"b": 2}), ((1,),)],
    ids=repr,
)
def test_tuples_refused(value):
    with pytest.raises(CanonicalizationError, match="tuple"):
        canonicalize_body({"a": value})
    with pytest.raises(CanonicalizationError, match="tuple"):
        canonicalize_body([value])


@pytest.mark.parametrize(
    "value",
    [b"x", bytearray(b"x"), {1, 2}, frozenset(), decimal.Decimal("1"), object(),
     types.MappingProxyType({"a": 1}), 1j],
    ids=lambda v: type(v).__name__,
)
def test_other_non_json_types_refused(value):
    with pytest.raises(CanonicalizationError):
        canonicalize_body({"a": value})


def test_dict_and_list_subclasses_accepted():
    od = collections.OrderedDict([("b", 1), ("a", [2])])
    assert canonicalize_body(od) == '{"a":[2],"b":1}'


def test_non_str_key_refused():
    with pytest.raises(CanonicalizationError):
        canonicalize_body({1: "a"})


def test_adjacent_surrogate_pair_in_value_refused():
    with pytest.raises(CanonicalizationError, match="surrogate"):
        canonicalize_body({"a": "x\ud83d\ude00y"})


def test_adjacent_surrogate_pair_in_key_refused():
    with pytest.raises(CanonicalizationError, match="surrogate"):
        canonicalize_body({"\ud83d\ude00": 1})


def test_lone_surrogates_still_escaped_like_js():
    assert canonicalize_body({"a": "\ud800x"}) == '{"a":"\\ud800x"}'
    # low then high is NOT a pair: both lone, both escaped
    assert canonicalize_body({"a": "\udc00\ud800"}) == '{"a":"\\udc00\\ud800"}'


def test_real_astral_char_unchanged():
    assert canonicalize_body({"a": "\U0001F600"}) == '{"a":"\U0001F600"}'


def test_envelope_refuses_bad_body_before_touching_wallet(tmp_path):
    # tmp_path has no wallet; the body check must fire first.
    with pytest.raises(CanonicalizationError):
        signing.build_signed_envelope({"a": 1.5}, "seat-a", seats_dir=tmp_path)


# -- N5: seat id, nonce, alg, timestamp, clock offset ---------------------------------


@pytest.mark.parametrize("seat", ["", "   ", "..", ".", "../x", "a/b", "a\\b", "C:x", "a\x00b"])
def test_bad_seat_id_refused(seat, tmp_path):
    with pytest.raises(SigningError, match="seat"):
        signing.preflight_seat(seat, seats_dir=tmp_path)
    with pytest.raises(SigningError, match="seat"):
        signing.build_signed_envelope({"a": 1}, seat, seats_dir=tmp_path)


def _fake_seat(tmp_path, seat="seat-a"):
    (tmp_path / seat).mkdir()
    (tmp_path / seat / "wallet.enc").write_text("{}", encoding="utf-8")
    (tmp_path / ".master-key").write_bytes(b"\x00" * 32)
    return seat


def test_preflight_missing_files(tmp_path):
    pytest.importorskip("eth_account")
    pytest.importorskip("cryptography")
    with pytest.raises(SigningError, match="wallet.enc"):
        signing.preflight_seat("seat-a", seats_dir=tmp_path)
    (tmp_path / "seat-a").mkdir()
    (tmp_path / "seat-a" / "wallet.enc").write_text("{}", encoding="utf-8")
    with pytest.raises(SigningError, match="master-key"):
        signing.preflight_seat("seat-a", seats_dir=tmp_path)
    (tmp_path / ".master-key").write_bytes(b"\x00" * 32)
    signing.preflight_seat("seat-a", seats_dir=tmp_path)


def test_iso_timestamp_floor():
    assert signing._iso_ms(0) == "1970-01-01T00:00:00.000Z"
    assert signing._iso_ms(1999) == "1970-01-01T00:00:01.999Z"
    assert signing._iso_ms(1_700_000_000_123) == "2023-11-14T22:13:20.123Z"


@pytest.fixture
def no_offset_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HOLOMESH_SIGNING_CLOCK_OFFSET_MS", raising=False)
    monkeypatch.setenv("HOLOMESH_CLOCK_OFFSET_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setattr(signing.time, "time", lambda: 1000.0)
    return tmp_path


@pytest.mark.parametrize(
    "raw,expected",
    [("1500", 1_001_500), ("-2000", 998_000), ("1.9", 1_000_001), (" 12 ", 1_000_012),
     ("", 1_000_000), ("abc", 1_000_000), ("NaN", 1_000_000), ("Infinity", 1_000_000),
     ("1_000", 1_000_000), ("0x10", 1_000_016)],
)
def test_clock_offset_env(no_offset_file, monkeypatch, raw, expected):
    monkeypatch.setenv("HOLOMESH_SIGNING_CLOCK_OFFSET_MS", raw)
    assert signing.signing_now_ms() == expected


def test_clock_offset_file(no_offset_file, monkeypatch):
    f = no_offset_file / "off.json"
    f.write_text(json.dumps({"schema": "holomesh.clock-offset.v1", "offsetMs": 250}), encoding="utf-8")
    monkeypatch.setenv("HOLOMESH_CLOCK_OFFSET_FILE", str(f))
    assert signing.signing_now_ms() == 1_000_250
    f.write_text(json.dumps({"schema": "other", "offsetMs": 250}), encoding="utf-8")
    assert signing.signing_now_ms() == 1_000_000
    f.write_text("not json", encoding="utf-8")
    assert signing.signing_now_ms() == 1_000_000
    assert signing.signing_now_ms(env={}) >= 0  # never throws


# -- signer policy (mcp_server._client delegates here) -------------------------------


def test_signer_from_env_unset_refuses():
    with pytest.raises(SigningError, match="HOLOMESH_ALLOW_UNSIGNED"):
        signing.signer_from_env({})
    with pytest.raises(SigningError):
        signing.signer_from_env({"HOLOMESH_SEAT_ID": "  ", "HOLOMESH_ALLOW_UNSIGNED": "true"})


def test_signer_from_env_allow_unsigned_opt_in():
    assert signing.signer_from_env({"HOLOMESH_ALLOW_UNSIGNED": "1"}) is None


def test_signer_from_env_seat(tmp_path):
    pytest.importorskip("eth_account")
    pytest.importorskip("cryptography")
    seat = _fake_seat(tmp_path)
    s = signing.signer_from_env({"HOLOMESH_SEAT_ID": seat, "HOLOMESH_ALLOW_UNSIGNED": "1"}, seats_dir=tmp_path)
    assert callable(s)
    with pytest.raises(SigningError, match="wallet.enc"):
        signing.signer_from_env({"HOLOMESH_SEAT_ID": "other-seat"}, seats_dir=tmp_path)
