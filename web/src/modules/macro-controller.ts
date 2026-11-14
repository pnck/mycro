import { headers } from "../utils/api";
import { configStore } from "../store";

export function pollMacroStatus(): void {
	const outEl = document.getElementById("macroOutput");
	fetch("/macro/status", { headers: headers() })
		.then(r => r.text())
		.then(t => {
			if (outEl) outEl.textContent = "Status: " + t;
			if (t.startsWith("running")) setTimeout(pollMacroStatus, 500);
		})
		.catch(() => {});
}

export function executeMacro(codeOverride?: string): void {
	const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
	const outEl = document.getElementById("macroOutput");
	const holdMsEl = document.getElementById("holdMs") as HTMLInputElement | null;
	const code = codeOverride !== undefined ? codeOverride : (codeEl?.value || "");
	const holdMs = holdMsEl?.value || "";

	if (outEl) outEl.textContent = "Working...";
	fetch("/macro", {
		method: "POST",
		headers: headers(),
		body: "code=" + encodeURIComponent(code) + "&hold_ms=" + encodeURIComponent(holdMs)
	})
		.then(r => r.text())
		.then(t => {
			if (outEl) outEl.textContent = t;
			if (t.startsWith("Started")) setTimeout(pollMacroStatus, 300);
		})
		.catch(e => {
			if (outEl) outEl.textContent = "Error: " + e;
		});
}

export function abortMacro(): void {
	const outEl = document.getElementById("macroOutput");
	fetch("/macro/abort", { method: "POST", headers: headers() })
		.then(r => r.text())
		.then(t => {
			if (outEl) outEl.textContent = t;
			setTimeout(pollMacroStatus, 300);
		})
		.catch(e => {
			if (outEl) outEl.textContent = "Error: " + e;
		});
}

export function initMacroController(): void {
	const form = document.getElementById("macroForm") as HTMLFormElement | null;
	const compileBtn = document.getElementById("compileBtn");
	const abortBtn = document.getElementById("abortBtn");
	const holdMsEl = document.getElementById("holdMs") as HTMLInputElement | null;
	const tokenEl = document.getElementById("token") as HTMLInputElement | null;

	if (!form) return;

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

	compileBtn?.addEventListener("click", () => {
		const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
		const outEl = document.getElementById("macroOutput");
		if (outEl) outEl.textContent = "Compiling...";
		fetch("/compile", {
			method: "POST",
			headers: headers(),
			body: "code=" + encodeURIComponent(codeEl?.value || "")
		})
			.then(r => r.text())
			.then(t => { if (outEl) outEl.textContent = t; })
			.catch(e => { if (outEl) outEl.textContent = "Error: " + e; });
	});

	abortBtn?.addEventListener("click", abortMacro);
}
