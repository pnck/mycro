/**
 * Canonical Free-Layout Homomorphic Formatter for MYCRO DSL v4.
 *
 * Operational Axiom:
 * The formatter ALWAYS and EXCLUSIVELY operates in `free` layout:
 * 1. Homomorphic Normalization: Transforms non-free source into equivalent free layout,
 *    mapping raw newlines to explicit \enter and raw spaces to \space (or \text{} payloads),
 *    absorbing line continuations, and establishing the `\#!syntax: free` preamble.
 * 2. Hierarchical AST Indentation: Formats statements with standard 2-space indentation
 *    based on brace nesting. In free layout, indentation spaces are completely absorbed
 *    by the compiler (guaranteeing ZERO stray space keystrokes emitted on hardware).
 * 3. Keystroke Consolidation: Consolidates continuous character typing into \text{}...\endtext{}
 *    and single keystrokes into \tap{key}. Verbatim text blocks are strictly preserved.
 */

function readMatching(text: string, startPos: number, openCh: string, closeCh: string): { inner: string; endPos: number } | null {
	if (text[startPos] !== openCh) return null;
	let depth = 1;
	let i = startPos + 1;
	const len = text.length;
	while (i < len && depth > 0) {
		const c = text[i];
		if (c === "\\" && i + 1 < len && (text[i + 1] === "\\" || text[i + 1] === "{" || text[i + 1] === "}")) {
			i += 2;
			continue;
		}
		if (c === openCh) depth++;
		else if (c === closeCh) depth--;
		i++;
	}
	if (depth !== 0) return null;
	return {
		inner: text.slice(startPos + 1, i - 1),
		endPos: i
	};
}

const BODY_COMMANDS = new Set(["\\def", "\\rep", "\\if", "\\guarded", "\\pixelrect"]);

function formatStatementStream(rawText: string, depth: number, isTypewriter: boolean): string[] {
	const lines: string[] = [];
	const pad = "  ".repeat(depth);
	let i = 0;
	const len = rawText.length;
	let pendingChars: string[] = [];

	function flushChars(): boolean {
		if (pendingChars.length === 0) return false;
		const str = pendingChars.join("");
		pendingChars = [];
		if (str.trim().length === 0 && !isTypewriter) {
			return false;
		}
		lines.push(`${pad}\\text{}${str}\\endtext{}`);
		return true;
	}

	while (i < len) {
		const c = rawText[i];

		// 1. Line continuation: \ + newline
		if (c === "\\" && (rawText[i + 1] === "\n" || (rawText[i + 1] === "\r" && rawText[i + 2] === "\n"))) {
			flushChars();
			i += (rawText[i + 1] === "\r" ? 3 : 2);
			continue;
		}

		// 2. Comments: \# to newline
		if (c === "\\" && rawText[i + 1] === "#") {
			flushChars();
			const nl = rawText.indexOf("\n", i);
			const cmt = nl === -1 ? rawText.slice(i).trim() : rawText.slice(i, nl).trim();
			lines.push(`${pad}${cmt}`);
			i = nl === -1 ? len : nl + 1;
			continue;
		}

		// 3. Verbatim blocks: \text{}...\endtext{}
		if (rawText.startsWith("\\text{}", i)) {
			flushChars();
			const endIdx = rawText.indexOf("\\endtext{}", i + 7);
			if (endIdx !== -1) {
				const fullBlock = rawText.slice(i, endIdx + 10);
				lines.push(`${pad}${fullBlock}`);
				i = endIdx + 10;
				continue;
			}
		}

		// 4. Backslash commands
		if (c === "\\") {
			const m = rawText.slice(i).match(/^\\([a-zA-Z][a-zA-Z0-9_]*)/);
			if (m) {
				flushChars();
				const cmd = m[0];
				let cursor = i + cmd.length;
				let decorator = "";
				const args: string[] = [];

				while (cursor < len && (rawText[cursor] === " " || rawText[cursor] === "\t")) cursor++;

				if (cmd === "\\def") {
					// 1. Read {name}
					if (cursor < len && rawText[cursor] === "{") {
						const nameRes = readMatching(rawText, cursor, "{", "}");
						if (nameRes) {
							args.push(nameRes.inner);
							cursor = nameRes.endPos;
							while (cursor < len && (rawText[cursor] === " " || rawText[cursor] === "\t")) cursor++;
						}
					}
					// 2. Read optional [argc]
					if (cursor < len && rawText[cursor] === "[") {
						const decRes = readMatching(rawText, cursor, "[", "]");
						if (decRes) {
							decorator = `[${decRes.inner.trim()}]`;
							cursor = decRes.endPos;
							while (cursor < len && (rawText[cursor] === " " || rawText[cursor] === "\t")) cursor++;
						}
					}
					// 3. Read {body}
					if (cursor < len && rawText[cursor] === "{") {
						const bodyRes = readMatching(rawText, cursor, "{", "}");
						if (bodyRes) {
							args.push(bodyRes.inner);
							cursor = bodyRes.endPos;
						}
					}
				} else {
					// Other commands: check optional [ ... ] first
					if (cursor < len && rawText[cursor] === "[") {
						const decRes = readMatching(rawText, cursor, "[", "]");
						if (decRes) {
							decorator = `[${decRes.inner.trim()}]`;
							cursor = decRes.endPos;
							while (cursor < len && (rawText[cursor] === " " || rawText[cursor] === "\t")) cursor++;
						}
					}

					while (cursor < len && rawText[cursor] === "{") {
						const blockRes = readMatching(rawText, cursor, "{", "}");
						if (!blockRes) break;
						args.push(blockRes.inner);
						cursor = blockRes.endPos;
						while (cursor < len && (rawText[cursor] === " " || rawText[cursor] === "\t")) cursor++;
					}
				}

				if (BODY_COMMANDS.has(cmd)) {
					const formatBlock = (header: string, inner1: string, inner2?: string) => {
						lines.push(`${pad}${header}`);
						lines.push(...formatStatementStream(inner1, depth + 1, false));
						if (inner2 !== undefined) {
							lines.push(`${pad}}{`);
							lines.push(...formatStatementStream(inner2, depth + 1, false));
						}
						lines.push(`${pad}}`);
					};

					if (cmd === "\\def") {
						formatBlock(`\\def${args[0] ? `{${args[0].trim()}}` : ""}${decorator}{`, args[1] || "");
					} else if (cmd === "\\rep") {
						formatBlock(`\\rep{${args[0]?.trim() || "1"}}{`, args[1] || "");
					} else if (cmd === "\\if") {
						formatBlock(`\\if{ ${args[0]?.trim() || "{0, =, 0}"} }{`, args[1] || "", args[2]);
					} else if (cmd === "\\guarded") {
						formatBlock(`\\guarded${decorator}{ ${args[0]?.trim() || "{$err, =, 0}"} }{`, args[1] || "", args[2]);
					} else if (cmd === "\\pixelrect") {
						const dst = args.length > 0 ? `{${args[0].trim()}}` : "";
						const rowsInner = args.length > 1 ? args[1] : "";
						const rowLines = rowsInner.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
						lines.push(`${pad}\\pixelrect${dst}{`);
						for (const rl of rowLines) {
							lines.push(`${pad}  ${rl}`);
						}
						lines.push(`${pad}}`);
					}
				} else {
					const formattedArgs = args.map(a => `{${a.trim()}}`).join("");
					lines.push(`${pad}${cmd}${decorator}${formattedArgs}`);
				}

				i = cursor;
				continue;
			}
		}

		// 5. Escapes
		if (c === "\\" && (rawText[i + 1] === "\\" || rawText[i + 1] === "{" || rawText[i + 1] === "}" || rawText[i + 1] === "$")) {
			pendingChars.push(rawText.slice(i, i + 2));
			i += 2;
			continue;
		}

		// 6. Whitespace and newlines
		if (c === "\n") {
			const hadText = flushChars();
			if (hadText && isTypewriter) {
				lines.push(`${pad}\\enter`);
			}
			i++;
			continue;
		}
		if (c === " " || c === "\t") {
			if (isTypewriter) {
				pendingChars.push(" ");
			}
			i++;
			continue;
		}
		if (c === "\r") {
			i++;
			continue;
		}

		// Regular character
		pendingChars.push(c);
		i++;
	}

	const hadTrailing = flushChars();
	if (hadTrailing && isTypewriter) {
		lines.push(`${pad}\\enter`);
	}
	return lines;
}

/**
 * Formats a given MYCRO macro script into canonical, elegant free layout DSL v4.
 */
export function formatMacroCode(code: string): string {
	const trimmed = code.trim();
	if (!trimmed) return "";

	let isTypewriter = true;
	let startIdx = 0;
	const rawLines = code.split(/\r?\n/);
	const leadComments: string[] = [];

	for (let idx = 0; idx < rawLines.length; idx++) {
		const line = rawLines[idx];
		const t = line.trim();
		if (t.startsWith("\\#!syntax:")) {
			const cfg = t.slice(10).trim();
			if (cfg === "free" || (cfg.includes("newline=ignore") && cfg.includes("space=ignore"))) {
				isTypewriter = false;
			} else {
				isTypewriter = true;
			}
			startIdx = idx + 1;
			break;
		} else if (t.startsWith("\\#")) {
			leadComments.push(t);
			startIdx = idx + 1;
		} else if (t === "") {
			startIdx = idx + 1;
		} else {
			break;
		}
	}

	const body = rawLines.slice(startIdx).join("\n");
	const formattedLines = formatStatementStream(body, 0, isTypewriter);

	const output: string[] = ["\\#!syntax: free"];
	if (leadComments.length > 0) {
		output.push(...leadComments);
	}
	output.push(...formattedLines);

	return output.join("\n") + "\n";
}
