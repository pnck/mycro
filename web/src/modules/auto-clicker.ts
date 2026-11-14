import { LONE_KEY_NAMES } from "./catalog";
import { executeMacro, abortMacro } from "./macro-controller";

function keyToMacro(k: string): string | null {
	k = k.trim().toLowerCase();
	if (!k) return null;
	if (k.length === 1) {
		if (k === "\\" || k === "{" || k === "}" || k === "$") return "\\" + k;
		const c = k.charCodeAt(0);
		return (c >= 32 && c <= 126) ? k : null;
	}
	return LONE_KEY_NAMES.has(k) ? "\\" + k : null;
}

function buildRep(n: number, body: string): string {
	if (n <= 255) return `\\rep{${n}}{${body}}`;
	const q = Math.floor(n / 255);
	const r = n % 255;
	return `\\rep{${q}}{\\rep{255}{${body}}}` + (r ? `\\rep{${r}}{${body}}` : "");
}

export function initAutoClicker(): void {
	const clType = document.getElementById("clType") as HTMLSelectElement | null;
	const clBtn = document.getElementById("clBtn") as HTMLSelectElement | null;
	const clKey = document.getElementById("clKey") as HTMLInputElement | null;
	const clCount = document.getElementById("clCount") as HTMLInputElement | null;
	const clStart = document.getElementById("clStart");
	const clAbort = document.getElementById("clAbort");
	const outEl = document.getElementById("macroOutput");

	if (!clType || !clBtn || !clKey || !clCount || !clStart || !clAbort) return;

	clType.addEventListener("change", () => {
		const kb = clType.value === "keyboard";
		clKey.style.display = kb ? "" : "none";
		clBtn.style.display = kb ? "none" : "";
	});

	clStart.addEventListener("click", () => {
		let body: string | null;
		if (clType.value === "mouse") {
			body = `\\click{${clBtn.value}}`;
		} else {
			body = keyToMacro(clKey.value);
			if (body === null) {
				if (outEl) outEl.textContent = "Invalid key: exactly ONE key — a single character (a-z, 0-9, symbol) or a key name (enter, esc, f1, ...)";
				return;
			}
		}
		const n = parseInt(clCount.value, 10);
		if (!(n >= 1 && n <= 65279)) {
			if (outEl) outEl.textContent = "Count must be 1..65279";
			return;
		}
		const code = buildRep(n, body);
		if (outEl) outEl.textContent = "Clicker: " + code;
		executeMacro(code);
	});

	clAbort.addEventListener("click", abortMacro);
}
