export interface MacroItem {
	name: string;
	code: string;
	updatedAt?: number;
}

const STORAGE_KEY = "mycro_user_macros";

function getStoredMacros(): MacroItem[] {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (Array.isArray(parsed)) {
			return parsed.filter(m => m && typeof m.name === "string" && typeof m.code === "string");
		}
	} catch {
		// Ignore storage/parse errors
	}
	return [];
}

function saveStoredMacros(macros: MacroItem[]): boolean {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(macros));
		return true;
	} catch {
		return false;
	}
}

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

	let macros = getStoredMacros();
	let activePresetName = "";

	function renderSelect(selectedName?: string): void {
		if (!select) return;
		activePresetName = selectedName !== undefined ? selectedName : (select.value || "");
		select.innerHTML = '<option value="">-- Saved Macros --</option>';
		for (const m of macros) {
			const opt = document.createElement("option");
			opt.value = m.name;
			opt.textContent = m.name;
			if (selectedName && m.name === selectedName) {
				opt.selected = true;
			}
			select.appendChild(opt);
		}
		if (selectedName !== undefined) {
			select.value = selectedName;
		}
		updateDeleteState();
	}

	function updateDeleteState(): void {
		if (!select || !nameInput || !deleteBtn) return;
		const currentName = select.value || nameInput.value.trim();
		const hasMatch = macros.some(m => m.name === currentName);
		deleteBtn.disabled = !hasMatch;
	}

	function handleSelectChange(): void {
		if (!select || !codeEl || !nameInput) return;
		const name = select.value;
		if (!name) {
			activePresetName = "";
			updateDeleteState();
			return;
		}
		const item = macros.find(m => m.name === name);
		if (!item) return;

		// Second confirmation if editor has existing content that differs from the target preset
		if (codeEl.value.trim() && codeEl.value !== item.code) {
			const ok = window.confirm(`Discard current editor content and load "${item.name}"?`);
			if (!ok) {
				select.value = activePresetName;
				updateDeleteState();
				return;
			}
		}

		codeEl.value = item.code;
		nameInput.value = item.name;
		activePresetName = item.name;
		updateDeleteState();
	}

	function handleSave(): void {
		if (!codeEl || !nameInput || !saveBtn) return;
		const code = codeEl.value;
		let name = nameInput.value.trim();
		if (!name) {
			if (!code.trim()) return;
			name = `Macro ${macros.length + 1}`;
			nameInput.value = name;
		}

		const existingIdx = macros.findIndex(m => m.name === name);
		const item: MacroItem = { name, code, updatedAt: Date.now() };
		if (existingIdx >= 0) {
			macros[existingIdx] = item;
		} else {
			macros.push(item);
		}
		saveStoredMacros(macros);
		renderSelect(name);

		const origText = saveBtn.textContent;
		saveBtn.textContent = "Saved";
		setTimeout(() => {
			if (saveBtn) saveBtn.textContent = origText;
		}, 800);
	}

	function handleDelete(): void {
		if (!select || !nameInput) return;
		const name = select.value || nameInput.value.trim();
		if (!name) return;
		macros = macros.filter(m => m.name !== name);
		saveStoredMacros(macros);
		nameInput.value = "";
		renderSelect("");
	}

	function handleExport(): void {
		if (!codeEl || !nameInput) return;
		const exportData = {
			app: "mycro",
			version: 1,
			exportedAt: new Date().toISOString(),
			macros: macros.length > 0 ? macros : (codeEl.value.trim() ? [{ name: nameInput.value.trim() || "untitled", code: codeEl.value }] : [])
		};
		if (exportData.macros.length === 0) return;

		const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
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
				const text = String(reader.result || "");
				const parsed = JSON.parse(text);
				let importedList: MacroItem[] = [];

				if (Array.isArray(parsed)) {
					importedList = parsed.filter(m => m && typeof m.name === "string" && typeof m.code === "string");
				} else if (parsed && Array.isArray(parsed.macros)) {
					importedList = parsed.macros.filter((m: any) => m && typeof m.name === "string" && typeof m.code === "string");
				} else if (parsed && typeof parsed === "object") {
					if (typeof parsed.name === "string" && typeof parsed.code === "string") {
						importedList = [{ name: parsed.name, code: parsed.code }];
					} else {
						for (const [k, v] of Object.entries(parsed)) {
							if (typeof v === "string") {
								importedList.push({ name: k, code: v });
							}
						}
					}
				}

				if (importedList.length === 0) return;

				for (const item of importedList) {
					const idx = macros.findIndex(m => m.name === item.name);
					if (idx >= 0) {
						macros[idx] = item;
					} else {
						macros.push(item);
					}
				}

				saveStoredMacros(macros);
				const last = importedList[importedList.length - 1];
				if (codeEl && codeEl.value.trim() && codeEl.value !== last.code) {
					const ok = window.confirm(`Discard current editor content and load imported "${last.name}"?`);
					if (ok) {
						if (nameInput) nameInput.value = last.name;
						codeEl.value = last.code;
						renderSelect(last.name);
					} else {
						renderSelect(activePresetName);
					}
				} else {
					if (nameInput) nameInput.value = last.name;
					if (codeEl) codeEl.value = last.code;
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

	const handleKeydown = (e: KeyboardEvent) => {
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
			e.preventDefault();
			handleSave();
		}
	};
	codeEl.addEventListener("keydown", handleKeydown);
	nameInput.addEventListener("keydown", handleKeydown);

	renderSelect();
}
