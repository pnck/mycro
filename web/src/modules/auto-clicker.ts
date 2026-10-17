import { headers } from "../utils/api";

const KEY_NAMES = (
	"enter return esc escape tab space bs backspace del delete up down left right " +
	"home end pgup pgdn ins caps ctrl shift alt win gui cmd " +
	"f1 f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12"
).split(" ");

function keyToMacro(k: string): string | null {
	k = k.trim().toLowerCase();
	if (!k) return null;
	if (k.length === 1) {
		if (k === "\\") return "\\\\";
		if (k === "{") return "\\{";
		if (k === "}") return "\\}";
		const c = k.charCodeAt(0);
		if (c >= 32 && c <= 126) return k;
		return null;
	}
	if (KEY_NAMES.indexOf(k) !== -1) return "\\" + k;
	return null;
}

function buildRep(n: number, body: string): string {
	if (n <= 255) return "\\rep{" + n + "}{" + body + "}";
	const q = Math.floor(n / 255);
	const r = n % 255;
	let s = "\\rep{" + q + "}{\\rep{255}{" + body + "}}";
	if (r) s += "\\rep{" + r + "}{" + body + "}";
	return s;
}

export function initAutoClicker() {
	const clType = document.getElementById("clType") as HTMLSelectElement | null;
	const clBtn = document.getElementById("clBtn") as HTMLSelectElement | null;
	const clKey = document.getElementById("clKey") as HTMLInputElement | null;
	const clCount = document.getElementById("clCount") as HTMLInputElement | null;
	const clStart = document.getElementById("clStart") as HTMLButtonElement | null;
	const clAbort = document.getElementById("clAbort") as HTMLButtonElement | null;
	const outEl = document.getElementById("macroOutput") as HTMLElement | null;
	const holdMsEl = document.getElementById("holdMs") as HTMLInputElement | null;

	if (!clType || !clBtn || !clKey || !clCount || !clStart || !clAbort) return;

	clType.addEventListener("change", () => {
		const kb = clType.value === "keyboard";
		clKey.style.display = kb ? "" : "none";
		clBtn.style.display = kb ? "none" : "";
	});

	function pollStatus() {
		fetch("/macro/status", { headers: headers() })
			.then(r => r.text())
			.then(t => {
				if (outEl) outEl.textContent = "Status: " + t;
				if (t.indexOf("running") === 0) setTimeout(pollStatus, 500);
			})
			.catch(() => {});
	}

	clStart.addEventListener("click", () => {
		let body: string | null;
		if (clType.value === "mouse") {
			body = "\\click{" + clBtn.value + "}";
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

		const holdMs = holdMsEl?.value || "";
		fetch("/macro", {
			method: "POST",
			headers: headers(),
			body: "code=" + encodeURIComponent(code) + "&hold_ms=" + encodeURIComponent(holdMs)
		})
			.then(r => r.text())
			.then(t => {
				if (outEl) outEl.textContent = t;
				if (t.indexOf("Started") === 0) setTimeout(pollStatus, 300);
			})
			.catch(e => {
				if (outEl) outEl.textContent = "Error: " + e;
			});
	});

	clAbort.addEventListener("click", () => {
		fetch("/macro/abort", { method: "POST", headers: headers() })
			.then(r => r.text())
			.then(t => {
				if (outEl) outEl.textContent = t;
				setTimeout(pollStatus, 300);
			})
			.catch(e => {
				if (outEl) outEl.textContent = "Error: " + e;
			});
	});
}
