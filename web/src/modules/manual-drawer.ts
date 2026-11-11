import { insertTextAtCursor } from "./editor";
import { drawerStore } from "../store";
import { MANUAL_DATA, ManualCategory, ManualItem } from "./manual-data";
import { initTokenInspector } from "./token-inspector";

export { MANUAL_DATA };
export type { ManualCategory, ManualItem };

export function initManualDrawer(): void {
	const drawer = document.getElementById("manualDrawer");
	const openBtn = document.getElementById("openManualBtn");
	const floatingBtn = document.getElementById("floatingManualBtn");
	const closeBtn = document.getElementById("closeManualBtn");
	const searchInput = document.getElementById("manualSearchInput") as HTMLInputElement | null;
	const clearSearchBtn = document.getElementById("manualSearchClear");
	const expandAllBtn = document.getElementById("manualExpandAllBtn");
	const collapseAllBtn = document.getElementById("manualCollapseAllBtn");
	const contentEl = document.getElementById("manualDrawerContent");

	if (!drawer || !contentEl) return;

	const savedState = drawerStore.get();

	// 1. Render reference manual categories and items
	renderManualContent(contentEl, savedState.collapsed);

	// 2. Initialize the Live Token Inspector panel above drawer body
	initTokenInspector();

	// 3. Restore open/closed state immediately without transition animation glitch
	if (savedState.isOpen) {
		drawer.classList.add("open", "no-transition");
	} else {
		drawer.classList.remove("open");
	}

	// Enable smooth transitions on subsequent interactions after first paint
	requestAnimationFrame(() => {
		drawer.classList.remove("no-transition");
		drawer.classList.add("ready");
	});

	function openDrawer(): void {
		drawer?.classList.add("open");
		searchInput?.focus();
		drawerStore.setKey("isOpen", true);
	}

	function closeDrawer(): void {
		drawer?.classList.remove("open");
		drawerStore.setKey("isOpen", false);
	}

	openBtn?.addEventListener("click", openDrawer);
	floatingBtn?.addEventListener("click", openDrawer);
	closeBtn?.addEventListener("click", closeDrawer);
	// NOTE: Backdrop click and Escape listeners are intentionally removed per spec:
	// The drawer is docked and persistent; only clicking the close button closes it.

	expandAllBtn?.addEventListener("click", () => {
		const sections = contentEl?.querySelectorAll<HTMLElement>(".manual-section") || [];
		sections.forEach(s => s.classList.remove("collapsed"));
		drawerStore.setKey("collapsed", []);
	});

	collapseAllBtn?.addEventListener("click", () => {
		const sections = contentEl?.querySelectorAll<HTMLElement>(".manual-section") || [];
		sections.forEach(s => s.classList.add("collapsed"));
		drawerStore.setKey("collapsed", MANUAL_DATA.map(c => c.name));
	});

	// Handle Enter key inside assemble input fields
	contentEl.addEventListener("keydown", (e: KeyboardEvent) => {
		if (e.key === "Enter") {
			const target = e.target as HTMLElement;
			if (target.classList.contains("manual-form-input")) {
				e.preventDefault();
				const itemRow = target.closest(".manual-item") as HTMLElement | null;
				const insertBtn = itemRow?.querySelector(".manual-insert-btn") as HTMLElement | null;
				if (insertBtn) {
					insertBtn.click();
				}
			}
		}
	});

	// Event delegation for snippet assemble & insert
	contentEl.addEventListener("click", (e: MouseEvent) => {
		const target = e.target as HTMLElement;

		// 1. Insert button click ONLY (no badge or code block triggers)
		const insertTarget = target.closest(".manual-insert-btn") as HTMLElement | null;
		if (!insertTarget) return;

		const itemRow = insertTarget.closest(".manual-item") as HTMLElement | null;
		if (!itemRow) return;

		const itemIndex = parseInt(itemRow.getAttribute("data-item-index") || "-1", 10);
		const catIndex = parseInt(itemRow.getAttribute("data-cat-index") || "-1", 10);
		if (catIndex < 0 || itemIndex < 0) return;

		const item = MANUAL_DATA[catIndex]?.items[itemIndex];
		if (!item) return;

		// Assemble text from interactive form fields or template
		const assembledSnippet = assembleSnippet(itemRow, item);
		if (!assembledSnippet) return;

		insertTextAtCursor(assembledSnippet);

		// Visual feedback
		const btn = itemRow.querySelector(".manual-insert-btn") as HTMLElement | null;
		if (btn) {
			btn.classList.add("inserted");
			const prevText = btn.textContent;
			btn.textContent = "✓";
			setTimeout(() => {
				btn.classList.remove("inserted");
				if (prevText) btn.textContent = prevText;
			}, 600);
		}
	});

	// Search & Filter
	function filterItems(query: string): void {
		const q = query.trim().toLowerCase();
		const sections = contentEl?.querySelectorAll<HTMLElement>(".manual-section") || [];
		let totalVisible = 0;

		sections.forEach(sec => {
			const items = sec.querySelectorAll<HTMLElement>(".manual-item");
			let sectionVisibleCount = 0;

			items.forEach(item => {
				const text = item.getAttribute("data-search") || "";
				if (!q || text.includes(q)) {
					item.style.display = "";
					sectionVisibleCount++;
					totalVisible++;
				} else {
					item.style.display = "none";
				}
			});

			sec.style.display = sectionVisibleCount > 0 ? "" : "none";
			if (q && sectionVisibleCount > 0) {
				sec.classList.remove("collapsed");
			}
		});

		const emptyMsg = contentEl?.querySelector<HTMLElement>(".manual-empty-search");
		if (emptyMsg) {
			emptyMsg.style.display = totalVisible === 0 ? "block" : "none";
		}

		if (clearSearchBtn) {
			clearSearchBtn.style.display = q.length > 0 ? "block" : "none";
		}
	}

	searchInput?.addEventListener("input", () => {
		filterItems(searchInput.value);
	});

	clearSearchBtn?.addEventListener("click", () => {
		if (searchInput) {
			searchInput.value = "";
			filterItems("");
			searchInput.focus();
		}
	});
}

function assembleSnippet(itemEl: HTMLElement, item: ManualItem): string {
	const inputs = itemEl.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-field]");
	const values: Record<string, string> = {};
	inputs.forEach(input => {
		const fieldName = input.getAttribute("data-field");
		if (fieldName) {
			values[fieldName] = input.value;
		}
	});

	// 1. Alias select variation (e.g. \enter vs \return)
	if (values._alias) {
		return `\\${values._alias}`;
	}

	// 2. Custom template assembly
	if (item.template) {
		let text = item.template;

		// Special case for \click with count
		if (item.name.includes("\\click")) {
			const btn = values.btn || "L";
			const count = values.count?.trim();
			if (count && count !== "1") {
				return `\\click{${btn}, ${count}}`;
			}
			return `\\click{${btn}}`;
		}

		// Replace placeholders ${key} with field values
		for (const [k, v] of Object.entries(values)) {
			text = text.split(`\${${k}}`).join(v);
		}
		return text;
	}

	// 3. Fallback to static snippet
	return item.snippet;
}

function renderManualContent(container: HTMLElement, collapsedList: string[] = []): void {
	container.innerHTML = "";

	MANUAL_DATA.forEach((cat, catIdx) => {
		const sec = document.createElement("div");
		sec.className = "manual-section";
		if (collapsedList.includes(cat.name)) {
			sec.classList.add("collapsed");
		}

		const header = document.createElement("div");
		header.className = "manual-section-header";
		header.innerHTML = `
			<div class="manual-section-title-wrapper">
				<span class="manual-section-chevron">▾</span>
				<span class="manual-section-name">${escapeHtml(cat.name)}</span>
			</div>
			<span class="manual-section-badge">${escapeHtml(cat.badge)}</span>
		`;
		header.addEventListener("click", () => {
			sec.classList.toggle("collapsed");
			const nowCollapsed = sec.classList.contains("collapsed");
			const cur = drawerStore.get().collapsed;
			const set = new Set(cur);
			if (nowCollapsed) {
				set.add(cat.name);
			} else {
				set.delete(cat.name);
			}
			drawerStore.setKey("collapsed", Array.from(set));
		});
		sec.appendChild(header);

		const list = document.createElement("div");
		list.className = "manual-item-list";

		cat.items.forEach((item, itemIdx) => {
			const row = document.createElement("div");
			row.className = item.multiline ? "manual-item manual-item-multiline" : "manual-item";
			row.setAttribute("data-cat-index", String(catIdx));
			row.setAttribute("data-item-index", String(itemIdx));

			// Construct comprehensive search index
			const paramSearch = (item.params || []).map(p => `${p.name} ${p.type} ${p.desc} ${p.range || ""}`).join(" ");
			const aliasSearch = (item.aliases || []).join(" ");
			const searchText = `${item.name} ${item.syntax || ""} ${item.desc} ${item.snippet} ${aliasSearch} ${item.usageId || ""} ${item.returns || ""} ${paramSearch} ${item.keywords || ""} ${cat.name}`.toLowerCase();
			row.setAttribute("data-search", searchText);

			// Render Aliases, Usage ID, Returns badges
			const metaParts: string[] = [];
			if (item.aliases && item.aliases.length > 0) {
				metaParts.push(`
					<div class="manual-meta-row">
						<span class="manual-meta-label">Aliases:</span>
						${item.aliases.map(a => `<span class="manual-alias-badge">${escapeHtml(a)}</span>`).join("")}
					</div>
				`);
			}
			if (item.usageId) {
				metaParts.push(`<span class="manual-usage-badge">HID: ${escapeHtml(item.usageId)}</span>`);
			}
			if (item.returns) {
				metaParts.push(`<span class="manual-return-badge">→ ${escapeHtml(item.returns)}</span>`);
			}
			const metaHtml = metaParts.length > 0 ? `<div class="manual-meta-row">${metaParts.join("")}</div>` : "";

			// Render Parameters box
			let paramsHtml = "";
			if (item.params && item.params.length > 0) {
				const paramItems = item.params.map(p => `
					<li class="manual-param-item">
						<span class="manual-param-name">${escapeHtml(p.name)}</span>
						<span class="manual-param-type">${escapeHtml(p.type)}</span>
						${p.range ? `<span class="manual-param-range">[${escapeHtml(p.range)}]</span>` : ""}
						${p.optional ? `<span class="manual-param-opt">(optional)</span>` : ""}
						<span class="manual-param-desc">${escapeHtml(p.desc)}</span>
					</li>
				`).join("");
				paramsHtml = `
					<div class="manual-params-box">
						<div class="manual-params-title">Parameters:</div>
						<ul class="manual-params-list">${paramItems}</ul>
					</div>
				`;
			}

			// Render Interactive Assemble Row (inputs, selects, and assemble insertion)
			let assembleHtml = "";
			const formControls: string[] = [];

			// 1. If item has aliases, provide alias dropdown
			if (item.aliases && item.aliases.length > 0 && !item.fields) {
				const canonical = item.snippet.replace(/^\\/, "").trim();
				const allOptions = Array.from(new Set([canonical, ...item.aliases]));
				const options = allOptions.map(a => `
					<option value="${escapeAttr(a)}">\\${escapeHtml(a)}</option>
				`).join("");
				formControls.push(`
					<div class="manual-form-field">
						<span class="manual-form-label">Variant:</span>
						<select data-field="_alias" class="manual-alias-select">${options}</select>
					</div>
				`);
			}

			// 2. If item has configurable parameter fields
			if (item.fields && item.fields.length > 0) {
				item.fields.forEach(f => {
					if (f.type === "select" && f.options) {
						const optHtml = f.options.map(opt => `
							<option value="${escapeAttr(opt)}" ${opt === f.default ? "selected" : ""}>${escapeHtml(opt)}</option>
						`).join("");
						formControls.push(`
							<div class="manual-form-field">
								<span class="manual-form-label">${escapeHtml(f.name)}:</span>
								<select data-field="${escapeAttr(f.name)}" class="manual-form-select">${optHtml}</select>
							</div>
						`);
					} else {
						const widthStyle = f.width ? `style="width:${escapeAttr(f.width)}"` : "";
						formControls.push(`
							<div class="manual-form-field">
								<span class="manual-form-label">${escapeHtml(f.name)}:</span>
								<input type="text" data-field="${escapeAttr(f.name)}" value="${escapeAttr(f.default)}" placeholder="${escapeAttr(f.placeholder || f.name)}" class="manual-form-input" ${widthStyle}>
							</div>
						`);
					}
				});
			}

			if (formControls.length > 0) {
				assembleHtml = `
					<div class="manual-assemble-row">
						<div class="manual-assemble-fields">${formControls.join("")}</div>
						<button type="button" class="manual-insert-btn" title="Assemble &amp; insert at cursor">➕</button>
					</div>
				`;
			}

			// Render Item Layout
			const isRecipe = cat.badge === "Recipes";
			const displayBadge = isRecipe ? "Recipe" : (item.syntax || item.snippet.replace(/\n$/, ""));

			if (item.multiline) {
				row.innerHTML = `
					<div class="manual-item-header">
						<div class="manual-item-headline">
							<span class="manual-code-pill">${escapeHtml(displayBadge)}</span>
							${isRecipe ? `<span class="manual-recipe-title">${escapeHtml(item.name)}</span>` : ""}
						</div>
						<div class="manual-item-actions">
							${formControls.length === 0 ? `<button type="button" class="manual-insert-btn" title="Insert snippet at cursor">➕</button>` : ""}
						</div>
					</div>
					<div class="manual-item-desc">${escapeHtml(item.desc)}</div>
					${metaHtml}
					${paramsHtml}
					${assembleHtml}
					<pre class="manual-code-block">${escapeHtml(item.snippet.trim())}</pre>
				`;
			} else {
				row.innerHTML = `
					<div class="manual-item-header">
						<div class="manual-item-headline">
							<span class="manual-code-pill">${escapeHtml(displayBadge)}</span>
							${isRecipe ? `<span class="manual-recipe-title">${escapeHtml(item.name)}</span>` : ""}
						</div>
						<div class="manual-item-actions">
							${formControls.length === 0 ? `<button type="button" class="manual-insert-btn" title="Insert at cursor">➕</button>` : ""}
						</div>
					</div>
					<div class="manual-item-desc">${escapeHtml(item.desc)}</div>
					${metaHtml}
					${paramsHtml}
					${assembleHtml}
				`;
			}

			list.appendChild(row);
		});

		sec.appendChild(list);
		container.appendChild(sec);
	});

	const emptyEl = document.createElement("div");
	emptyEl.className = "manual-empty-search";
	emptyEl.style.display = "none";
	emptyEl.textContent = "No matching commands, parameters, aliases or recipes found.";
	container.appendChild(emptyEl);
}

function escapeHtml(str: string): string {
	return str
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;");
}

function escapeAttr(str: string): string {
	return str
		.replace(/&/g, "&amp;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;");
}
