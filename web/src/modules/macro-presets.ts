import { presetsStore, activePresetStore, StoredMacro } from "../store";

export function initMacroPresets(): void {
	const select = document.getElementById("macroPresetSelect") as HTMLSelectElement | null;
	const nameInput = document.getElementById("macroPresetName") as HTMLInputElement | null;
	const saveBtn = document.getElementById("macroSaveBtn") as HTMLButtonElement | null;
	const deleteBtn = document.getElementById("macroDeleteBtn") as HTMLButtonElement | null;
	const exportBtn = document.getElementById("macroExportBtn") as HTMLButtonElement | null;
	const importBtn = document.getElementById("macroImportBtn") as HTMLButtonElement | null;
	const importFile = document.getElementById("macroImportFile") as HTMLInputElement | null;
	const codeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;

	if (!select || !nameInput || !saveBtn || !deleteBtn || !exportBtn || !importBtn || !importFile || !codeEl) {
		return;
	}

	let macros = presetsStore.get();
	let activePresetName = activePresetStore.get() || "";
	if (activePresetName) {
		nameInput.value = activePresetName;
	}

	function updateDeleteState(): void {
		const currentName = select.value || nameInput.value.trim();
		deleteBtn.disabled = !macros.some(m => m.name === currentName);
	}

	function renderSelect(selectedName?: string): void {
		activePresetName = selectedName !== undefined ? selectedName : (select.value || activePresetStore.get() || "");
		activePresetStore.set(activePresetName);
		select.innerHTML = '<option value="">-- Saved Macros --</option>' +
			macros.map(m => `<option value="${m.name}" ${m.name === activePresetName ? "selected" : ""}>${m.name}</option>`).join("");
		if (selectedName !== undefined) select.value = selectedName;
		updateDeleteState();
	}

	function handleSelectChange(): void {
		const name = select.value;
		if (!name) {
			activePresetName = "";
			activePresetStore.set("");
			updateDeleteState();
			return;
		}
		const item = macros.find(m => m.name === name);
		if (!item) return;

		if (codeEl.value.trim() && codeEl.value !== item.code && !window.confirm(`Discard current editor content and load "${item.name}"?`)) {
			select.value = activePresetName;
			updateDeleteState();
			return;
		}

		codeEl.value = item.code;
		nameInput.value = item.name;
		activePresetName = item.name;
		activePresetStore.set(item.name);
		updateDeleteState();
	}

	function handleSave(): void {
		const code = codeEl.value;
		let name = nameInput.value.trim();
		if (!name) {
			if (!code.trim()) return;
			name = `Macro ${macros.length + 1}`;
			nameInput.value = name;
		}

		const existingIdx = macros.findIndex(m => m.name === name);
		const item: StoredMacro = { name, code, updatedAt: Date.now() };
		if (existingIdx >= 0) macros[existingIdx] = item;
		else macros.push(item);
		presetsStore.set(macros);
		activePresetStore.set(name);
		renderSelect(name);

		const origText = saveBtn.textContent;
		saveBtn.textContent = "Saved";
		setTimeout(() => { saveBtn.textContent = origText; }, 800);
	}

	function handleDelete(): void {
		const name = select.value || nameInput.value.trim();
		if (!name) return;
		macros = macros.filter(m => m.name !== name);
		presetsStore.set(macros);
		nameInput.value = "";
		activePresetStore.set("");
		renderSelect("");
	}

	function handleExport(): void {
		const exportList = macros.length > 0 ? macros : (codeEl.value.trim() ? [{ name: nameInput.value.trim() || "untitled", code: codeEl.value }] : []);
		if (exportList.length === 0) return;

		const blob = new Blob([JSON.stringify({ app: "mycro", version: 1, exportedAt: new Date().toISOString(), macros: exportList }, null, 2)], { type: "application/json" });
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `mycro-macros-${new Date().toISOString().slice(0, 10)}.json`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}

	function handleImportFile(file: File): void {
		const reader = new FileReader();
		reader.onload = () => {
			try {
				const parsed = JSON.parse(String(reader.result || ""));
				const raw = Array.isArray(parsed) ? parsed : (Array.isArray(parsed?.macros) ? parsed.macros : (parsed?.name ? [parsed] : Object.entries(parsed || {}).map(([name, code]) => ({ name, code }))));
				const importedList: StoredMacro[] = raw.filter((m: any) => m && typeof m.name === "string" && typeof m.code === "string");
				if (importedList.length === 0) return;

				for (const item of importedList) {
					const idx = macros.findIndex(m => m.name === item.name);
					if (idx >= 0) macros[idx] = item;
					else macros.push(item);
				}

				presetsStore.set(macros);
				const last = importedList[importedList.length - 1];
				if (codeEl.value.trim() && codeEl.value !== last.code && !window.confirm(`Discard current editor content and load imported "${last.name}"?`)) {
					renderSelect(activePresetName);
				} else {
					nameInput.value = last.name;
					codeEl.value = last.code;
					activePresetStore.set(last.name);
					renderSelect(last.name);
				}
			} catch (err) {
				console.error("Failed to import macros:", err);
			}
		};
		reader.readAsText(file);
	}

	select.addEventListener("change", handleSelectChange);
	saveBtn.addEventListener("click", handleSave);
	deleteBtn.addEventListener("click", handleDelete);
	exportBtn.addEventListener("click", handleExport);
	importBtn.addEventListener("click", () => importFile.click());
	importFile.addEventListener("change", () => {
		const file = importFile.files?.[0];
		if (file) {
			handleImportFile(file);
			importFile.value = "";
		}
	});

	nameInput.addEventListener("input", updateDeleteState);
	nameInput.addEventListener("keydown", (e: KeyboardEvent) => {
		if (e.key === "Enter") {
			e.preventDefault();
			handleSave();
		}
	});

	window.addEventListener("keydown", (e: KeyboardEvent) => {
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
			e.preventDefault();
			handleSave();
		}
	});

	renderSelect(activePresetName);
}
