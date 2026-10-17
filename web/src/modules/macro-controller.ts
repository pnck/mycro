import { headers } from "../utils/api";

export function initMacroController() {
	const form = document.getElementById("macroForm") as HTMLFormElement | null;
	const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;
	const outEl = document.getElementById("macroOutput") as HTMLElement | null;
	const compileBtn = document.getElementById("compileBtn") as HTMLButtonElement | null;
	const abortBtn = document.getElementById("abortBtn") as HTMLButtonElement | null;
	const holdMsEl = document.getElementById("holdMs") as HTMLInputElement | null;
	const executeBtn = form?.querySelector("input[type=submit]") as HTMLInputElement | null;

	if (!form || !codeEl || !outEl || !compileBtn || !abortBtn) return;

	let inFlight = false;

	function setBusy(busy: boolean) {
		inFlight = busy;
		if (executeBtn) executeBtn.disabled = busy;
		if (compileBtn) compileBtn.disabled = busy;
	}

	function pollStatus() {
		fetch("/macro/status", { headers: headers() })
			.then(r => r.text())
			.then(t => {
				if (outEl) outEl.textContent = "Status: " + t;
				if (t.indexOf("running") === 0) setTimeout(pollStatus, 500);
			})
			.catch(() => {});
	}

	function post(path: string, codeOverride?: string) {
		if (inFlight) return;
		setBusy(true);
		if (outEl) outEl.textContent = "Working...";
		const code = codeOverride !== undefined ? codeOverride : (codeEl?.value || "");
		const holdMs = holdMsEl?.value || "";

		fetch(path, {
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
			})
			.finally(() => setBusy(false));
	}

	function doAbort() {
		fetch("/macro/abort", { method: "POST", headers: headers() })
			.then(r => r.text())
			.then(t => {
				if (outEl) outEl.textContent = t;
				setTimeout(pollStatus, 300);
			})
			.catch(e => {
				if (outEl) outEl.textContent = "Error: " + e;
			});
	}

	form.addEventListener("submit", e => {
		e.preventDefault();
		post("/macro");
	});

	compileBtn.addEventListener("click", () => post("/compile"));
	abortBtn.addEventListener("click", doAbort);
}
