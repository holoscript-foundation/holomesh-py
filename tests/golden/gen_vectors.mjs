// Golden-vector generator for holomesh/signing.py.
//
// Runs the REAL ai-ecosystem hooks/lib/holomesh-signing.mjs (canonicalizeBody,
// buildSignedEnvelope) and records its output as tests/golden/signing_vectors.json.
// The Python side (tests/test_signing_golden.py) must reproduce every byte.
//
// Usage (see tests/golden/README.md):
//   AI_ECOSYSTEM_DIR=<git-archive copy of ai-ecosystem> node gen_vectors.mjs <out.json>
// The archive needs hooks/lib/holomesh-{signing,clock,eip191}.mjs,
// scripts/lib/ecosystem-root.mjs and node_modules/@noble/{curves,hashes}.
//
// TEST-ONLY key material: private key = 0x00..01, master key = 32 x 0x42, iv = 12 x 0x07.
// These are public dummy values built from a formula, never a real seat secret.

import { createCipheriv, createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.env.AI_ECOSYSTEM_DIR;
if (!root) throw new Error('set AI_ECOSYSTEM_DIR to the ai-ecosystem archive root');
const outPath = process.argv[2];
if (!outPath) throw new Error('usage: node gen_vectors.mjs <out.json>');

const signingPath = join(root, 'hooks', 'lib', 'holomesh-signing.mjs');
const eip191Path = join(root, 'hooks', 'lib', 'holomesh-eip191.mjs');
const signing = await import(pathToFileURL(signingPath).href);
const eip = await import(pathToFileURL(eip191Path).href);

// ---- canonicalizeBody vectors. Inputs are JSON TEXT so both runtimes parse identically. ----
const canonCases = [
  ['empty-object', '{}'],
  ['empty-array', '[]'],
  ['null', 'null'],
  ['true', 'true'],
  ['false', 'false'],
  ['string-plain', '"hello"'],
  ['string-empty', '""'],
  ['int-zero', '0'],
  ['int-negative', '-17'],
  ['int-max-safe', '9007199254740991'],
  ['int-min-safe', '-9007199254740991'],
  ['int-negative-zero-text', '-0'],
  ['key-order-simple', '{"b":1,"a":2,"c":3}'],
  ['key-order-case-and-digits', '{"b":1,"a":2,"B":3,"_":4,"10":5,"9":6,"A":7,"1":8}'],
  ['key-order-nested', '{"z":{"y":1,"x":{"w":2,"v":3}},"a":[{"d":1,"c":2},{"b":3,"a":4}]}'],
  ['array-order-preserved', '[3,1,2,{"b":1,"a":2},[9,8]]'],
  ['nested-deep', '{"a":{"b":{"c":{"d":{"e":[1,[2,[3,[4]]]]}}}}}'],
  ['mixed-types', '{"s":"x","n":1,"b":true,"z":null,"l":[],"o":{}}'],
  ['unicode-latin', '{"k":"h\\u00e9llo w\\u00f6rld","\\u00e9":1}'],
  ['unicode-cjk', '{"\\u540d\\u524d":"\\u30c6\\u30b9\\u30c8","msg":"\\u4f60\\u597d"}'],
  ['unicode-emoji-astral', '{"e":"\\ud83d\\ude00 ok \\ud83d\\udc4d"}'],
  ['unicode-combining', '{"e":"e\\u0301","f":"\\u00e9"}'],
  ['unicode-rtl', '{"a":"\\u05e9\\u05dc\\u05d5\\u05dd \\u0645\\u0631\\u062d\\u0628\\u0627"}'],
  ['unicode-key-order-bmp-vs-astral', '{"\\uffff":1,"\\ud83d\\ude00":2,"\\ue000":3,"a":4}'],
  ['unicode-key-order-astral-pair', '{"\\ud83d\\ude00":1,"\\ud83c\\udf00":2,"\\uff5e":3}'],
  ['unicode-lone-surrogate-value', '{"v":"a\\ud800b"}'],
  ['unicode-lone-surrogate-key', '{"\\udc00k":1}'],
  ['unicode-line-separators', '{"v":"a\\u2028b\\u2029c"}'],
  ['escapes-quote-backslash', '{"v":"say \\"hi\\" \\\\ path\\\\to"}'],
  ['escapes-control-named', '{"v":"a\\nb\\tc\\rd\\be\\ff"}'],
  ['escapes-control-unnamed', '{"v":"\\u0000\\u0001\\u001f\\u007f"}'],
  ['escapes-slash', '{"v":"a/b</script>"}'],
  ['key-needs-escape', '{"a\\"b":1,"c\\\\d":2,"e\\nf":3}'],
  ['float-fractional-same', '{"a":0.5,"b":-2.25,"c":1.5}'],
  ['float-exp-1e21-same-format', '{"a":1e21}'],
  ['float-fractional-0.1', '{"a":0.1,"b":123456.789}'],
  ['signed-payload-shape', '{"body":{"task":"t1","tags":["x","y"],"n":3},"nonce":"00112233445566778899aabbccddeeff","timestamp":"2026-01-02T03:04:05.006Z"}'],
];

// Known float divergences (documented caveat). JS output is recorded; Python is asserted
// NOT to match for these so the caveat stays visible and an accidental change is noticed.
const floatCases = [
  ['float-integral-1.0', '{"a":1.0}'],
  ['float-integral-100.0', '{"a":100.0}'],
  ['float-neg-zero', '{"a":-0.0}'],
  ['float-1e16', '{"a":1e16}'],
  ['float-small-1e-7', '{"a":1e-7}'],
  ['float-exp-notation-1E2', '{"a":1E2}'],
];

const canon = (cases) =>
  cases.map(([name, inputJson]) => ({
    name,
    input_json: inputJson,
    expected: signing.canonicalizeBody(JSON.parse(inputJson)),
  }));

// ---- signed envelope vectors from a test seat wallet.enc ----
const TEST_PRIVKEY = '0x' + '00'.repeat(31) + '01';
const MASTER_KEY = Buffer.alloc(32, 0x42);
const IV = Buffer.alloc(12, 0x07);
const SEAT_ID = 'claude-test-00000000-default-x402';

const cipher = createCipheriv('aes-256-gcm', MASTER_KEY, IV);
const ct = Buffer.concat([cipher.update(TEST_PRIVKEY, 'utf8'), cipher.final()]);
const tag = cipher.getAuthTag();
const address = eip.addressFromPrivateKey(TEST_PRIVKEY);
const walletBlob = {
  address,
  encrypted_privkey: {
    alg: 'aes-256-gcm',
    iv: IV.toString('base64'),
    ct: ct.toString('base64'),
    tag: tag.toString('base64'),
  },
};

const seatsDir = mkdtempSync(join(tmpdir(), 'holomesh-golden-'));
mkdirSync(join(seatsDir, SEAT_ID), { recursive: true });
writeFileSync(join(seatsDir, SEAT_ID, 'wallet.enc'), JSON.stringify(walletBlob));
writeFileSync(join(seatsDir, '.master-key'), MASTER_KEY);

const envelopeCases = [
  ['simple', { task: 't1', n: 3 }, '00112233445566778899aabbccddeeff', 1700000000123],
  ['epoch-zero', { a: 1 }, 'ffeeddccbbaa99887766554433221100', 0],
  ['one-ms', { a: 1 }, '0102030405060708090a0b0c0d0e0f10', 1],
  ['ms-999', { a: 1 }, '1111111111111111aaaaaaaaaaaaaaaa', 1700000000999],
  ['whole-second', { a: 1 }, '2222222222222222bbbbbbbbbbbbbbbb', 1700000001000],
  ['year-2099', { a: 1 }, '3333333333333333cccccccccccccccc', 4070908800000],
  ['nested-unicode', { title: 'h\u00e9llo \u540d\u524d \ud83d\ude00', tags: ['b', 'a'], meta: { z: 1, y: [{ d: 1, c: 2 }] } }, '4444444444444444dddddddddddddddd', 1789000000321],
  ['key-order-astral', { '\uffff': 1, '\ud83d\ude00': 2, a: 3 }, '5555555555555555eeeeeeeeeeeeeeee', 1790000000456],
  ['empty-body', {}, '66666666666666660000000000000000', 1791000000789],
];
const envelopes = [];
for (const [name, body, nonce, nowMs] of envelopeCases) {
  const envelope = await signing.buildSignedEnvelope(body, { seatId: SEAT_ID, seatsDir, nonce, nowMs });
  envelopes.push({ name, body_json: JSON.stringify(body), nonce, now_ms: nowMs, envelope });
}
rmSync(seatsDir, { recursive: true, force: true });

const out = {
  schema: 'holomesh-py.signing-golden/v1',
  generated_by: 'tests/golden/gen_vectors.mjs',
  source: {
    file: 'hooks/lib/holomesh-signing.mjs',
    sha256: createHash('sha256').update(readFileSync(signingPath)).digest('hex'),
    ai_ecosystem_commit: process.env.GEN_SOURCE_SHA || null,
    node: process.version,
  },
  test_key: {
    note: 'TEST-ONLY dummy key material, rebuilt by formula in the Python test, never a real seat.',
    private_key_formula: '0x + 00*31 + 01',
    master_key_formula: '32 bytes of 0x42',
    iv_formula: '12 bytes of 0x07',
    seat_id: SEAT_ID,
    wallet_enc: walletBlob,
  },
  canonicalize: canon(canonCases),
  canonicalize_float_divergences: canon(floatCases),
  envelopes,
};
writeFileSync(outPath, JSON.stringify(out, null, 2) + '\n');
console.log(`wrote ${outPath}: ${out.canonicalize.length} canonicalize, ${out.canonicalize_float_divergences.length} float, ${envelopes.length} envelopes`);