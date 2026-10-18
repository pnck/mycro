#!/usr/bin/env node
/**
 * Automated test suite for pure-TS HMAC-SHA256 implementation.
 * Validates against RFC 4231 standard test vectors and fuzzes against Node.js crypto.
 */

import crypto from "node:crypto";
import assert from "node:assert";
import { hmacSha256Hex, hmacSha256 } from "../src/utils/crypto.ts";

console.log("Running HMAC-SHA256 test suite...");

// RFC 4231 Case 1: 20-byte key (0x0b), "Hi There"
assert.strictEqual(
	hmacSha256Hex(new Uint8Array(20).fill(0x0b), "Hi There"),
	"b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
	"RFC 4231 Case 1 failed"
);

// RFC 4231 Case 2: key "Jefe", "what do ya want for nothing?"
assert.strictEqual(
	hmacSha256Hex("Jefe", "what do ya want for nothing?"),
	"5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
	"RFC 4231 Case 2 failed"
);

// RFC 4231 Case 3: 20-byte key (0xaa), 50-byte data (0xdd)
assert.strictEqual(
	hmacSha256Hex(new Uint8Array(20).fill(0xaa), new Uint8Array(50).fill(0xdd)),
	"773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe",
	"RFC 4231 Case 3 failed"
);

// RFC 4231 Case 4: 25-byte key (0x01..0x19), 50-byte data (0xcd)
const key4 = new Uint8Array(25);
for (let i = 0; i < 25; i++) key4[i] = i + 1;
assert.strictEqual(
	hmacSha256Hex(key4, new Uint8Array(50).fill(0xcd)),
	"82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b",
	"RFC 4231 Case 4 failed"
);

// RFC 4231 Case 6: 131-byte key (> 64 bytes, requires SHA-256 pre-hash)
assert.strictEqual(
	hmacSha256Hex(new Uint8Array(131).fill(0xaa), "Test Using Larger Than Block-Size Key - Hash Key First"),
	"60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
	"RFC 4231 Case 6 failed"
);

// RFC 4231 Case 7: 131-byte key (> 64 bytes) and large message
assert.strictEqual(
	hmacSha256Hex(
		new Uint8Array(131).fill(0xaa),
		"This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm."
	),
	"9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
	"RFC 4231 Case 7 failed"
);

// Edge Cases
assert.strictEqual(
	hmacSha256Hex("", ""),
	crypto.createHmac("sha256", "").update("").digest("hex"),
	"Empty key and empty message failed"
);

assert.strictEqual(
	hmacSha256Hex("my_secret_token", ""),
	crypto.createHmac("sha256", "my_secret_token").update("").digest("hex"),
	"Non-empty key with empty message failed"
);

assert.strictEqual(
	hmacSha256Hex("", "sample_nonce_payload"),
	crypto.createHmac("sha256", "").update("sample_nonce_payload").digest("hex"),
	"Empty key with non-empty message failed"
);

// Cross-check 500 random inputs against Node.js crypto
for (let i = 0; i < 500; i++) {
	const k = crypto.randomBytes(Math.floor(Math.random() * 120));
	const m = crypto.randomBytes(Math.floor(Math.random() * 250));
	const exp = crypto.createHmac("sha256", k).update(m).digest("hex");
	const act = hmacSha256Hex(k, m);
	assert.strictEqual(act, exp, `Fuzz mismatch on iteration ${i}`);
}

console.log("✓ RFC 4231 Test Cases 1, 2, 3, 4, 6, 7 and 500 fuzzing tests passed successfully.");
