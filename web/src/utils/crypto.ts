/**
 * Compact pure-TypeScript HMAC-SHA256 implementation.
 * Zero external dependencies, runs reliably in both secure and non-secure (HTTP LAN) contexts.
 */

function sha256Bytes(data: Uint8Array): Uint8Array {
	function rightRotate(value: number, amount: number): number {
		return (value >>> amount) | (value << (32 - amount));
	}

	const mathPow = Math.pow;
	const maxWord = mathPow(2, 32);
	let i: number, j: number;
	const words: number[] = [];
	const bitLength = data.length * 8;
	let hash: number[] = [];
	const k: number[] = [];
	let primeCounter = 0;
	const isComposite: Record<number, number> = {};

	for (let candidate = 2; primeCounter < 64; candidate++) {
		if (!isComposite[candidate]) {
			for (i = 0; i < 313; i += candidate) isComposite[i] = candidate;
			hash[primeCounter] = (mathPow(candidate, 0.5) * maxWord) | 0;
			k[primeCounter++] = (mathPow(candidate, 1 / 3) * maxWord) | 0;
		}
	}

	const len = data.length;
	for (i = 0; i < len; i++) {
		words[i >> 2] |= data[i] << ((3 - (i % 4)) * 8);
	}
	words[len >> 2] |= 0x80 << ((3 - (len % 4)) * 8);
	const totalWords = (((len + 8) >> 6) + 1) * 16;
	words[totalWords - 2] = (bitLength / maxWord) | 0;
	words[totalWords - 1] = bitLength | 0;

	for (j = 0; j < totalWords; ) {
		const w = words.slice(j, (j += 16));
		const oldHash = hash;
		hash = hash.slice(0, 8);
		for (i = 0; i < 64; i++) {
			const w15 = w[i - 15],
				w2 = w[i - 2];
			const a = hash[0],
				e = hash[4];
			const temp1 =
				hash[7] +
				(rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25)) +
				((e & hash[5]) ^ (~e & hash[6])) +
				k[i] +
				(w[i] =
					i < 16
						? w[i] | 0
						: (w[i - 16] +
								(rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3)) +
								w[i - 7] +
								(rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))) |
							0);
			const temp2 =
				(rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22)) +
				((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));
			hash = [(temp1 + temp2) | 0].concat(hash);
			hash[4] = (hash[4] + temp1) | 0;
		}
		for (i = 0; i < 8; i++) {
			hash[i] = (hash[i] + oldHash[i]) | 0;
		}
	}

	const out = new Uint8Array(32);
	for (i = 0; i < 8; i++) {
		for (j = 3; j >= 0; j--) {
			out[i * 4 + (3 - j)] = (hash[i] >> (j * 8)) & 255;
		}
	}
	return out;
}

export function hmacSha256(keyInput: string | Uint8Array, msgInput: string | Uint8Array): Uint8Array {
	const enc = new TextEncoder();
	let key = typeof keyInput === "string" ? enc.encode(keyInput) : keyInput;
	if (key.length > 64) {
		key = sha256Bytes(key);
	}
	const oKeyPad = new Uint8Array(64);
	const iKeyPad = new Uint8Array(64);
	for (let i = 0; i < 64; i++) {
		const b = i < key.length ? key[i] : 0;
		oKeyPad[i] = b ^ 0x5c;
		iKeyPad[i] = b ^ 0x36;
	}
	const msg = typeof msgInput === "string" ? enc.encode(msgInput) : msgInput;
	const inner = new Uint8Array(64 + msg.length);
	inner.set(iKeyPad, 0);
	inner.set(msg, 64);
	const innerHash = sha256Bytes(inner);

	const outer = new Uint8Array(64 + 32);
	outer.set(oKeyPad, 0);
	outer.set(innerHash, 64);
	return sha256Bytes(outer);
}

export function hmacSha256Hex(keyInput: string | Uint8Array, msgInput: string | Uint8Array): string {
	const outerHash = hmacSha256(keyInput, msgInput);
	return Array.from(outerHash)
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("");
}

