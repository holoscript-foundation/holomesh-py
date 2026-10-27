# Signing golden vectors

`signing_vectors.json` pins `holomesh/signing.py` to the real
`hooks/lib/holomesh-signing.mjs` in ai-ecosystem. The vectors were produced by running that JS;
`tests/test_signing_golden.py` asserts Python reproduces every byte (canonical JSON, timestamp
format, AES-GCM `wallet.enc` decryption, EIP-191 signature, full envelope).

The fixture's `source` block records the ai-ecosystem commit and the sha256 of the JS file used.
If the JS changes, regenerate and review the diff; a changed vector means the wire format changed.

## Regenerate

1. Make a git-archive copy of ai-ecosystem in a fresh temp folder (only these paths are needed):
   `git archive HEAD hooks/lib/holomesh-signing.mjs hooks/lib/holomesh-clock.mjs hooks/lib/holomesh-eip191.mjs scripts/lib/ecosystem-root.mjs | tar -x -C <tmp>`
2. Put `@noble/curves` and `@noble/hashes` in `<tmp>/node_modules/@noble/` (copy from an ai-ecosystem install).
3. `AI_ECOSYSTEM_DIR=<tmp> GEN_SOURCE_SHA=<ai-ecosystem commit> node tests/golden/gen_vectors.mjs tests/golden/signing_vectors.json`

## Test-only keys

The signing key is `0x` + 31 zero bytes + `01`, the master key is 32 bytes of `0x42`, the AES-GCM iv is 12 bytes of `0x07`.
They are public dummy values rebuilt by formula in the test; no real seat secret is stored here.

## Float caveat

JS `JSON.stringify(1.0)` is `1`; Python `json.dumps(1.0)` is `1.0`, and exponent forms differ (`1e16`, `1e-7`).
Signed bodies must use integers. The `canonicalize_float_divergences` vectors pin the known divergences
(the Python test asserts they still differ, so the caveat cannot silently change). Integers above 2**53 cannot
be represented in JS at all. `NaN`/`Infinity` serialize as `null` in JS and must never appear in a body.

## Running

`pip install -e ".[signing]" pytest && pytest`. Envelope tests are skipped without the `signing` extra.