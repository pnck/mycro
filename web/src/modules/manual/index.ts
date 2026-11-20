import { insertTextAtCursor } from "../editor";
import { drawerStore } from "../../store";
import { MANUAL_DATA, ManualCategory, ManualItem } from "../catalog";
import { initTokenInspector } from "../token-inspector";

export { MANUAL_DATA };
export type { ManualCategory, ManualItem };

const PLUS_ICON = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 2v12M2 8h12" stroke-linecap="round"/></svg>`;
const CHECK_ICON = `<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8.5l3.5 3.5 6.5-7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export function escapeHtml(str: string): string {
	return str
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#039;");
}

export const escapeAttr = escapeHtml;

function renderDescWithLinks(rawText?: string): string {
	if (!rawText) return "";
	const parts = rawText.split(/\(See '([^']+)'\.\)/g);
	if (parts.length === 1) return escapeHtml(rawText);
	return parts.map((part, i) => i % 2 === 1
		? `<a href="#" class="manual-xref-link" data-section="${escapeAttr(part)}">(See '${escapeHtml(part)}'.)</a>`
		: escapeHtml(part)
	).join("");
}

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

	renderManualContent(contentEl, savedState.collapsed);
	initTokenInspector();

	if (savedState.isOpen) {
		drawer.classList.add("open", "no-transition");
	} else {
		drawer.classList.remove("open");
	}

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

	expandAllBtn?.addEventListener("click", () => {
		contentEl.querySelectorAll<HTMLElement>(".manual-section").forEach(s => s.classList.remove("collapsed"));
		drawerStore.setKey("collapsed", []);
	});

	collapseAllBtn?.addEventListener("click", () => {
		contentEl.querySelectorAll<HTMLElement>(".manual-section").forEach(s => s.classList.add("collapsed"));
		drawerStore.setKey("collapsed", MANUAL_DATA.map(c => c.name));
	});

	contentEl.addEventListener("keydown", (e: KeyboardEvent) => {
		if (e.key === "Enter") {
			const target = e.target as HTMLElement;
			if (target.classList.contains("manual-form-input")) {
				e.preventDefault();
				const itemRow = target.closest(".manual-item") as HTMLElement | null;
				(itemRow?.querySelector(".manual-insert-btn") as HTMLElement | null)?.click();
			}
		}
	});

	contentEl.addEventListener("click", (e: MouseEvent) => {
		const target = e.target as HTMLElement;

		const xref = target.closest(".manual-xref-link") as HTMLElement | null;
		if (xref) {
			e.preventDefault();
			const secName = xref.getAttribute("data-section");
			if (secName) {
				const sections = Array.from(contentEl.querySelectorAll<HTMLElement>(".manual-section"));
				const targetSec = sections.find(s => {
					return s.querySelector(".manual-section-name")?.textContent?.trim() === secName;
				});
				if (targetSec) {
					targetSec.classList.remove("collapsed");
					targetSec.scrollIntoView({ behavior: "smooth", block: "start" });
					targetSec.classList.add("manual-section-highlight");
					setTimeout(() => targetSec.classList.remove("manual-section-highlight"), 1200);
				}
			}
			return;
		}

		const header = target.closest(".manual-section-header") as HTMLElement | null;
		if (header) {
			const sec = header.closest(".manual-section");
			if (sec) {
				sec.classList.toggle("collapsed");
				const cur = new Set(drawerStore.get().collapsed);
				const name = header.querySelector(".manual-section-name")?.textContent || "";
				if (sec.classList.contains("collapsed")) cur.add(name);
				else cur.delete(name);
				drawerStore.setKey("collapsed", Array.from(cur));
			}
			return;
		}

		const insertBtn = target.closest(".manual-insert-btn") as HTMLButtonElement | null;
		if (!insertBtn) return;

		const itemRow = insertBtn.closest(".manual-item") as HTMLElement | null;
		if (!itemRow) return;

		const catIndex = parseInt(itemRow.getAttribute("data-cat-index") || "-1", 10);
		const itemIndex = parseInt(itemRow.getAttribute("data-item-index") || "-1", 10);
		if (catIndex < 0 || itemIndex < 0) return;

		const item = MANUAL_DATA[catIndex]?.items[itemIndex];
		if (!item) return;

		const assembledText = assembleSnippet(itemRow, item);
		if (!assembledText) return;

		insertTextAtCursor(assembledText);

		insertBtn.classList.add("inserted");
		const prev = insertBtn.innerHTML;
		insertBtn.innerHTML = CHECK_ICON;
		setTimeout(() => {
			insertBtn.classList.remove("inserted");
			if (prev) insertBtn.innerHTML = prev;
		}, 600);
	});

	function filterItems(query: string): void {
		const q = query.trim().toLowerCase();
		const sections = contentEl?.querySelectorAll<HTMLElement>(".manual-section") || [];
		let totalVisible = 0;

		sections.forEach(sec => {
			const items = sec.querySelectorAll<HTMLElement>(".manual-item");
			const intro = sec.querySelector<HTMLElement>(".manual-section-intro");
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

			if (intro) intro.style.display = (!q || sectionVisibleCount > 0) ? "" : "none";
			sec.style.display = sectionVisibleCount > 0 ? "" : "none";
			if (q && sectionVisibleCount > 0) sec.classList.remove("collapsed");
		});

		const emptyMsg = contentEl?.querySelector<HTMLElement>(".manual-empty-search");
		if (emptyMsg) emptyMsg.style.display = totalVisible === 0 ? "block" : "none";
		if (clearSearchBtn) clearSearchBtn.style.display = q.length > 0 ? "block" : "none";
	}

	searchInput?.addEventListener("input", () => filterItems(searchInput.value));
	clearSearchBtn?.addEventListener("click", () => {
		if (searchInput) {
			searchInput.value = "";
			filterItems("");
			searchInput.focus();
		}
	});
}

function renderInsertBtn(snippet: string, title = "Insert at cursor"): string {
	return `<button type="button" class="manual-insert-btn" data-snippet="${escapeAttr(snippet)}" title="${escapeAttr(title)}">${PLUS_ICON}</button>`;
}

function assembleSnippet(itemEl: HTMLElement, item: ManualItem): string {
	const inputs = itemEl.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-field]");
	const values: Record<string, string> = {};
	inputs.forEach(input => {
		const fieldName = input.getAttribute("data-field");
		if (fieldName) values[fieldName] = input.value;
	});

	if (values._alias) return `\\${values._alias}`;

	if (item.template) {
		let text = item.template;
		for (const [k, v] of Object.entries(values)) {
			text = text.replace(new RegExp(`\\$\\{${k}\\?([^:}]+):?([^}]*)\\}`, "g"), (_, trueVal, falseVal) => {
				return (v && v !== "1" && v !== "0") ? trueVal.split("$val").join(v) : (falseVal || "");
			});
			text = text.split(`\${${k}}`).join(v);
		}
		return text;
	}

	return item.snippet;
}

function renderCard(item: ManualItem, cat: ManualCategory, catIdx: number, itemIdx: number): string {
	const isRecipe = cat.badge === "Recipes";
	const isNorm = Boolean(item.notes?.length || item.table);
	const displayBadge = isRecipe ? "Recipe" : (item.syntax || item.snippet.replace(/\n$/, ""));

	const aliasSearch = (item.aliases || []).join(" ");
	const searchText = `${item.name} ${item.syntax || ""} ${item.desc} ${item.snippet} ${aliasSearch} ${item.usageId || ""} ${item.returns || ""} ${cat.name} ${cat.desc || ""}`.toLowerCase();

	const metaParts: string[] = [];
	if (item.aliases?.length) {
		metaParts.push(`<span class="manual-meta-label">Aliases:</span>${item.aliases.map(a => `<span class="manual-alias-badge">${escapeHtml(a)}</span>`).join("")}`);
	}
	if (item.usageId) metaParts.push(`<span class="manual-usage-badge">HID: ${escapeHtml(item.usageId)}</span>`);
	if (item.returns) metaParts.push(`<span class="manual-return-badge">→ ${escapeHtml(item.returns)}</span>`);
	const metaHtml = metaParts.length ? `<div class="manual-meta-row">${metaParts.join("")}</div>` : "";

	const formControls: string[] = [];
	if (item.aliases?.length && !item.fields) {
		const canonical = item.snippet.replace(/^\\/, "").trim();
		const allOptions = Array.from(new Set([canonical, ...item.aliases]));
		formControls.push(
			`<div class="manual-form-field"><span class="manual-form-label">Variant:</span><select data-field="_alias" class="manual-alias-select">${allOptions.map(a => `<option value="${escapeAttr(a)}">\\${escapeHtml(a)}</option>`).join("")}</select></div>`
		);
	}

	if (item.fields?.length) {
		for (const f of item.fields) {
			if (f.type === "select" && f.options) {
				formControls.push(
					`<div class="manual-form-field"><span class="manual-form-label">${escapeHtml(f.name)}:</span><select data-field="${escapeAttr(f.name)}" class="manual-form-select">${f.options.map(opt => `<option value="${escapeAttr(opt)}" ${opt === f.default ? "selected" : ""}>${escapeHtml(opt)}</option>`).join("")}</select></div>`
				);
			} else {
				const widthStyle = f.width ? `style="width:${escapeAttr(f.width)}"` : "";
				formControls.push(
					`<div class="manual-form-field"><span class="manual-form-label">${escapeHtml(f.name)}:</span><input type="text" data-field="${escapeAttr(f.name)}" value="${escapeAttr(f.default)}" placeholder="${escapeAttr(f.placeholder || f.name)}" class="manual-form-input" ${widthStyle}></div>`
				);
			}
		}
	}

	const assembleHtml = formControls.length ? `<div class="manual-assemble-row"><div class="manual-assemble-fields">${formControls.join("")}</div>${renderInsertBtn(item.snippet, "Assemble &amp; insert at cursor")}</div>` : "";

	const tableHtml = item.table ? `<div class="manual-table-wrap"><table class="manual-table"><thead><tr>${item.table.headers.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${item.table.rows.map(row => `<tr>${row.map(c => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";

	const notesHtml = item.notes?.length ? `<ul class="manual-notes-list">${item.notes.map(([tag, desc]) => `<li class="manual-note-item"><strong class="manual-note-tag">${escapeHtml(tag)}:</strong><span class="manual-note-desc">${escapeHtml(desc)}</span></li>`).join("")}</ul>` : "";

	const snippetHtml = (item.snippet && (item.multiline || isRecipe) && !isNorm) ? `<pre class="manual-code-block">${escapeHtml(item.snippet)}</pre>` : "";

	const headlineHtml = isRecipe
		? `<span class="manual-badge">Recipe</span><strong class="manual-card-title">${escapeHtml(item.name)}</strong>`
		: isNorm
		? `<span class="manual-badge manual-badge-norm">Norm</span><strong class="manual-card-title">${escapeHtml(item.name)}</strong>`
		: `<code class="manual-cmd-code">${escapeHtml(displayBadge)}</code>`;

	const canInsert = Boolean(item.snippet && !isNorm);
	const actionBtnHtml = (!assembleHtml && canInsert) ? renderInsertBtn(item.snippet) : "";

	return `<div class="${item.multiline ? "manual-item manual-item-multiline" : "manual-item"}" data-cat-index="${catIdx}" data-item-index="${itemIdx}" data-search="${escapeAttr(searchText)}"><div class="manual-item-header"><div class="manual-item-headline">${headlineHtml}</div><div class="manual-item-actions">${actionBtnHtml}</div></div><div class="manual-item-desc">${renderDescWithLinks(item.desc)}</div>${metaHtml}${assembleHtml}${tableHtml}${notesHtml}${snippetHtml}</div>`;
}

function renderManualContent(container: HTMLElement, collapsedList: string[] = []): void {
	container.innerHTML = MANUAL_DATA.map((cat, catIdx) => {
		const isCollapsed = collapsedList.includes(cat.name);
		const itemsHtml = cat.items.map((item, itemIdx) => renderCard(item, cat, catIdx, itemIdx)).join("");
		const introHtml = cat.desc ? `<div class="manual-section-intro"><div class="manual-section-desc">${renderDescWithLinks(cat.desc)}</div></div>` : "";
		return `<div class="manual-section${isCollapsed ? " collapsed" : ""}"><div class="manual-section-header"><div class="manual-section-title-wrapper"><span class="manual-section-chevron">▾</span><span class="manual-section-name">${escapeHtml(cat.name)}</span></div><span class="manual-section-badge">${escapeHtml(cat.badge)}</span></div><div class="manual-item-list">${introHtml}${itemsHtml}</div></div>`;
	}).join("") + '<div class="manual-empty-search" style="display:none">No matching commands found.</div>';
}
