import { headers } from "../utils/api";
import { configStore } from "../store";

const getOut = () => document.getElementById("macroOutput");

function postMacro(path: string, body?: string, initial?: string, onOk?: (t: string) => void): void {
	const out = getOut();
	if (initial && out) out.textContent = initial;
	fetch(path, { method: "POST", headers: headers(), body })
		.then(r => r.text())
		.then(t => {
			if (out) out.textContent = t;
			onOk?.(t);
		})
		.catch(e => {
			if (out) out.textContent = "Error: " + e;
		});
}

export function pollMacroStatus(): void {
	fetch("/macro/status", { headers: headers() })
		.then(r => r.text())
		.then(t => {
			const out = getOut();
			if (out) out.textContent = "Status: " + t;
			if (t.startsWith("running")) setTimeout(pollMacroStatus, 500);
		})
		.catch(() => {});
}

export function executeMacro(codeOverride?: string): void {
	const code = codeOverride !== undefined ? codeOverride : ((document.getElementById("macroCode") as HTMLTextAreaElement | null)?.value || "");
	const holdMs = (document.getElementById("holdMs") as HTMLInputElement | null)?.value || "";
	postMacro(
		"/macro",
		`code=${encodeURIComponent(code)}&hold_ms=${encodeURIComponent(holdMs)}`,
		"Working...",
		t => { if (t.startsWith("Started")) setTimeout(pollMacroStatus, 300); }
	);
}

export function abortMacro(): void {
	postMacro("/macro/abort", undefined, undefined, () => setTimeout(pollMacroStatus, 300));
}

export function initMacroController(): void {
	const form = document.getElementById("macroForm") as HTMLFormElement | null;
	if (!form) return;

	const holdMsEl = document.getElementById("holdMs") as HTMLInputElement | null;
	const tokenEl = document.getElementById("token") as HTMLInputElement | null;
	const cfg = configStore.get();
	if (tokenEl && cfg.token && !tokenEl.value) tokenEl.value = cfg.token;
	if (holdMsEl && cfg.holdMs && !holdMsEl.value) holdMsEl.value = String(cfg.holdMs);

	tokenEl?.addEventListener("input", () => configStore.setKey("token", tokenEl.value.trim()));
	holdMsEl?.addEventListener("input", () => {
		const n = parseInt(holdMsEl.value, 10);
		if (!isNaN(n)) configStore.setKey("holdMs", n);
	});

	form.addEventListener("submit", e => {
		e.preventDefault();
		executeMacro();
	});

	document.getElementById("compileBtn")?.addEventListener("click", () => {
		const code = (document.getElementById("macroCode") as HTMLTextAreaElement | null)?.value || "";
		postMacro("/compile", `code=${encodeURIComponent(code)}`, "Compiling...");
	});

	document.getElementById("abortBtn")?.addEventListener("click", abortMacro);
}

