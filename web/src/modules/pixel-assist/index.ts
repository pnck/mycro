import { getToken, headers } from "../../utils/api";
import { hmacSha256Hex } from "../../utils/crypto";
import { generatePointSnippet, generateRegionSnippet, toHex } from "./generator";
import { insertTextAtCursor } from "../editor";

const ZOOM_PRESETS = [0.25, 0.5, 1, 2, 4, 8, 16];

function setSvgRect(el: SVGElement, x: number, y: number, w: number, h: number): void {
	el.setAttribute("x", String(x));
	el.setAttribute("y", String(y));
	el.setAttribute("width", String(w));
	el.setAttribute("height", String(h));
}

export function initPixelAssist(): void {
	// --- DOM Element References ---
	const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T | null;
	const $req = <T extends Element = HTMLElement>(id: string) => document.getElementById(id) as T;

	const paOverlay = $req("paOverlay");
	const paViewport = $req("paViewport");
	const paShieldModal = $req("paShieldModal");
	const paStageContainer = $req("paStageContainer");
	const paDisplayCanvas = $req<HTMLCanvasElement>("paDisplayCanvas");
	const paDisplayCtx = paDisplayCanvas.getContext("2d")!;
	const paOverlaySvg = $req<SVGElement>("paOverlaySvg");
	const paPixelGridRect = $req<SVGRectElement>("paPixelGridRect");
	const paPixelGridPattern = $req<SVGPatternElement>("paPixelGridPattern");
	const paPixelGridPath = $req<SVGPathElement>("paPixelGridPath");
	const paSvgSelectionGroup = $req<SVGElement>("paSvgSelectionGroup");
	const paSvgBox = $req<SVGRectElement>("paSvgBox");
	const paSvgBoxOuter = $req<SVGRectElement>("paSvgBoxOuter");
	const paSvgBoxBody = $req<SVGRectElement>("paSvgBoxBody");
	const paSvgHandles = $req<SVGElement>("paSvgHandles");
	const handleEls = Array.from(paSvgHandles.querySelectorAll<SVGElement>(".pa-handle")).map(el => ({
		el,
		k: el.getAttribute("data-handle") || ""
	}));

	const paZoomSelect = $req<HTMLSelectElement>("paZoomSelect");
	const paZoomCustomOpt = $req<HTMLOptionElement>("paZoomCustomOpt");
	const paLoupeCanvas = $req<HTMLCanvasElement>("paLoupeCanvas");
	const paLoupeCtx = paLoupeCanvas.getContext("2d")!;
	paLoupeCtx.imageSmoothingEnabled = false;

	const paSnippetBox = $req<HTMLTextAreaElement>("paSnippetBox");
	const paInX = $req<HTMLInputElement>("paInX");
	const paInY = $req<HTMLInputElement>("paInY");
	const paInW = $req<HTMLInputElement>("paInW");
	const paInH = $req<HTMLInputElement>("paInH");

	const setText = (id: string, text: string) => {
		const el = $(id);
		if (el) el.textContent = text;
	};

	const syncColorDisplays = (hex: string, r: number, g: number, b: number) => {
		setText("paHexDslText", `0x${hex}`);
		setText("paHexCssText", `#${hex}`);
		setText("paRgbText", `rgb(${r}, ${g}, ${b})`);
	};

	// --- State ---
	let img: HTMLImageElement | null = null;
	let mode: "point" | "region" = "point";
	let point = { x: 0, y: 0 };
	let region = { x: 0, y: 0, w: 1, h: 1 };
	let panX = 0;
	let panY = 0;
	let zoom = 1;
	let showGrid = true;
	let shieldBlocked = false;
	let isOverlayOpen = false;
	let isSpaceDown = false;
	let isCapturing = false;

	// Offscreen Pixel Buffer
	const sourceCanvas = document.createElement("canvas");
	let sourceCtx: CanvasRenderingContext2D = sourceCanvas.getContext("2d", { willReadFrequently: true })!;
	let cachedPixels: Uint8ClampedArray | null = null;
	let cachedW = 0;
	let cachedH = 0;

	function initPixelBuffer(image: HTMLImageElement) {
		sourceCanvas.width = image.width;
		sourceCanvas.height = image.height;
		sourceCtx.drawImage(image, 0, 0);
		const d = sourceCtx.getImageData(0, 0, image.width, image.height);
		cachedPixels = d.data;
		cachedW = image.width;
		cachedH = image.height;
	}

	function getPixel(x: number, y: number): [number, number, number] {
		if (!cachedPixels || x < 0 || x >= cachedW || y < 0 || y >= cachedH) return [0, 0, 0];
		const i = (y * cachedW + x) * 4;
		return [cachedPixels[i], cachedPixels[i + 1], cachedPixels[i + 2]];
	}

	function checkAntiFingerprintNoise(): boolean {
		try {
			const c = document.createElement("canvas");
			c.width = 4;
			c.height = 4;
			const ctx = c.getContext("2d", { willReadFrequently: true })!;
			ctx.fillStyle = "rgb(100, 150, 200)";
			ctx.fillRect(0, 0, 4, 4);
			const first = ctx.getImageData(1, 1, 1, 1).data;
			if (first[0] !== 100 || first[1] !== 150 || first[2] !== 200) return true;
			for (let i = 0; i < 4; i++) {
				const next = ctx.getImageData(1, 1, 1, 1).data;
				if (next[0] !== first[0] || next[1] !== first[1] || next[2] !== first[2]) return true;
			}
			return false;
		} catch {
			return false;
		}
	}

	function enforceShield(locked: boolean) {
		shieldBlocked = locked;
		paShieldModal.style.display = locked ? "flex" : "none";
		const warn = $("paShieldWarning");
		if (warn) warn.style.display = locked ? "inline-flex" : "none";
		const res = $("paShieldModalResult");
		if (res) {
			res.style.display = locked ? "block" : "none";
			if (locked) {
				res.style.background = "#ffebe9";
				res.style.color = "#cf222e";
				res.textContent = "Color sampling is disabled until browser canvas noise protection is disabled.";
			}
		}
	}

	// --- Coordinate Math ---
	let viewportRect = { left: 0, top: 0, width: 800, height: 600 };
	function updateViewportRect() {
		const r = paViewport.getBoundingClientRect();
		viewportRect = { left: r.left, top: r.top, width: r.width, height: r.height };
	}

	function screenToPixel(clientX: number, clientY: number) {
		if (!img) return { x: 0, y: 0, inside: false };
		const vx = clientX - viewportRect.left;
		const vy = clientY - viewportRect.top;
		const sx = vx - panX;
		const sy = vy - panY;
		const w = img.width * zoom;
		const h = img.height * zoom;
		const inside = sx >= 0 && sx < w && sy >= 0 && sy < h;
		return {
			x: Math.max(0, Math.min(img.width - 1, Math.floor(sx / zoom))),
			y: Math.max(0, Math.min(img.height - 1, Math.floor(sy / zoom))),
			inside
		};
	}

	function applyZoomAt(focalX: number, focalY: number, newZoom: number) {
		if (!img) return;
		newZoom = Math.max(0.1, Math.min(32, newZoom));
		const stageX = (focalX - panX) / zoom;
		const stageY = (focalY - panY) / zoom;
		zoom = newZoom;
		panX = focalX - stageX * zoom;
		panY = focalY - stageY * zoom;
		updateZoomSelect(false);
		renderStage();
	}

	function zoomToFit() {
		if (!img) return;
		updateViewportRect();
		const vw = viewportRect.width || paViewport.clientWidth || 800;
		const vh = viewportRect.height || paViewport.clientHeight || 600;
		const margin = 48;
		const fit = Math.min((vw - margin * 2) / img.width, (vh - margin * 2) / img.height);
		zoom = Math.min(fit, 1);
		panX = Math.round((vw - img.width * zoom) / 2);
		panY = Math.round((vh - img.height * zoom) / 2);
		updateZoomSelect(true);
		renderStage();
	}

	function updateZoomSelect(isFit: boolean) {
		const pct = Math.round(zoom * 100);
		const exact = !isFit && ZOOM_PRESETS.find(z => Math.round(z * 100) === pct);
		if (exact !== undefined) {
			paZoomSelect.value = String(exact);
			paZoomCustomOpt.style.display = "none";
		} else {
			paZoomCustomOpt.value = "custom";
			paZoomCustomOpt.textContent = isFit ? `Fit (${pct}%)` : `${pct}%`;
			paZoomCustomOpt.style.display = "block";
			paZoomSelect.value = "custom";
		}
	}

	// --- Render Pipeline ---
	function renderStage() {
		if (!img) return;
		paStageContainer.style.transform = `translate(${Math.round(panX)}px, ${Math.round(panY)}px)`;
		const w = Math.round(img.width * zoom);
		const h = Math.round(img.height * zoom);
		paDisplayCanvas.style.width = w + "px";
		paDisplayCanvas.style.height = h + "px";
		paOverlaySvg.style.width = w + "px";
		paOverlaySvg.style.height = h + "px";
		renderSelection();
		renderPixelGrid();
	}

	function renderPixelGrid() {
		const btn = $("paGridToggleBtn");
		if (btn) {
			btn.style.color = showGrid ? "#0969da" : "#57606a";
			btn.style.borderColor = showGrid ? "#0969da" : "#d0d7de";
			btn.style.background = showGrid ? "#f0f7ff" : "#ffffff";
		}
		if (showGrid && zoom >= 3.9) {
			paPixelGridPattern.setAttribute("width", String(zoom));
			paPixelGridPattern.setAttribute("height", String(zoom));
			paPixelGridPath.setAttribute("d", `M ${zoom} 0 L 0 0 0 ${zoom}`);
			paPixelGridRect.style.display = "block";
		} else {
			paPixelGridRect.style.display = "none";
		}
	}

	function renderSelection() {
		if (!img) {
			paSvgSelectionGroup.style.display = "none";
			return;
		}
		paSvgSelectionGroup.style.display = "block";
		const isPoint = mode === "point";
		const x = isPoint ? point.x : region.x;
		const y = isPoint ? point.y : region.y;
		const w = isPoint ? 1 : region.w;
		const h = isPoint ? 1 : region.h;

		const sx = x * zoom;
		const sy = y * zoom;
		const sw = Math.max(1, w * zoom);
		const sh = Math.max(1, h * zoom);

		setSvgRect(paSvgBox, sx, sy, sw, sh);
		setSvgRect(paSvgBoxOuter, sx - 1, sy - 1, sw + 2, sh + 2);
		setSvgRect(paSvgBoxBody, sx, sy, sw, sh);
		paSvgBoxBody.style.pointerEvents = isPoint ? "none" : "all";

		paSvgHandles.style.display = isPoint ? "none" : "block";
		if (!isPoint) {
			const half = 3.5;
			const posMap: Record<string, [number, number]> = {
				nw: [sx, sy],
				n: [sx + sw / 2, sy],
				ne: [sx + sw, sy],
				e: [sx + sw, sy + sh / 2],
				se: [sx + sw, sy + sh],
				s: [sx + sw / 2, sy + sh],
				sw: [sx, sy + sh],
				w: [sx, sy + sh / 2]
			};
			for (const { el, k } of handleEls) {
				const pos = posMap[k];
				if (pos) {
					el.setAttribute("x", String(pos[0] - half));
					el.setAttribute("y", String(pos[1] - half));
				}
			}
		}
	}

	function renderLoupe(cx: number, cy: number) {
		if (!img || !cachedPixels) return;
		const r = 5;
		const cellSize = 12;
		paLoupeCtx.fillStyle = "#ffffff";
		paLoupeCtx.fillRect(0, 0, 132, 132);

		for (let dy = -r; dy <= r; dy++) {
			for (let dx = -r; dx <= r; dx++) {
				const px = cx + dx;
				const py = cy + dy;
				const cellX = (dx + r) * cellSize;
				const cellY = (dy + r) * cellSize;
				if (px >= 0 && px < img.width && py >= 0 && py < img.height) {
					const [cr, cg, cb] = getPixel(px, py);
					paLoupeCtx.fillStyle = `rgb(${cr},${cg},${cb})`;
					paLoupeCtx.fillRect(cellX, cellY, cellSize, cellSize);
				}
				paLoupeCtx.strokeStyle = "rgba(0,0,0,0.06)";
				paLoupeCtx.strokeRect(cellX, cellY, cellSize, cellSize);
			}
		}
		// Center Reticle: White 2px outer + Blue 1px inner
		const tx = r * cellSize;
		const ty = r * cellSize;
		paLoupeCtx.strokeStyle = "#ffffff";
		paLoupeCtx.lineWidth = 2;
		paLoupeCtx.strokeRect(tx + 1, ty + 1, cellSize - 2, cellSize - 2);
		paLoupeCtx.strokeStyle = "#0969da";
		paLoupeCtx.lineWidth = 1;
		paLoupeCtx.strokeRect(tx + 2, ty + 2, cellSize - 4, cellSize - 4);
	}

	function updateHoverPreview(x: number, y: number) {
		const [r, g, b] = getPixel(x, y);
		const hex = toHex(r, g, b);
		const swatch = $("paColorSwatch");
		if (swatch) swatch.style.backgroundColor = `rgb(${r},${g},${b})`;
		setText("paColorHexShort", `0x${hex}`);
	}

	function updateLoupeAndPreview(x: number, y: number) {
		renderLoupe(x, y);
		updateHoverPreview(x, y);
	}

	function syncRegionParams() {
		if (!img) return;
		paInX.value = String(region.x);
		paInY.value = String(region.y);
		paInW.value = String(region.w);
		paInH.value = String(region.h);
		setText("paCoordText", `Rect: ${region.x},${region.y} (${region.w}×${region.h})`);

		const targetX = region.x + (region.w >> 1);
		const targetY = region.y + (region.h >> 1);
		const [r, g, b] = getPixel(targetX, targetY);
		syncColorDisplays(toHex(r, g, b), r, g, b);

		const swatches = $("paRegionSwatches");
		if (swatches) {
			const x2 = region.x + region.w - 1;
			const y2 = region.y + region.h - 1;
			const pts: [string, number, number][] = [
				["TL", region.x, region.y],
				["TR", x2, region.y],
				["BL", region.x, y2],
				["BR", x2, y2],
				["Center", targetX, targetY]
			];
			swatches.innerHTML = pts.map(([name, px, py]) => {
				const [pr, pg, pb] = getPixel(px, py);
				const phex = toHex(pr, pg, pb);
				return `<span style="display:inline-flex;align-items:center;gap:3px;background:#f6f8fa;padding:2px 5px;border-radius:3px;font-size:10px;font-family:monospace;border:1px solid #d0d7de" title="${name}: (${px},${py}) 0x${phex}"><span style="width:9px;height:9px;border-radius:2px;background:rgb(${pr},${pg},${pb});display:inline-block;border:1px solid #d0d7de"></span> ${name}: 0x${phex}</span>`;
			}).join("");
		}

		paSnippetBox.value = generateRegionSnippet(region.x, region.y, region.w, region.h, getPixel);
	}

	function renderPinned() {
		if (!img) return;
		const isPoint = mode === "point";
		if (isPoint) {
			const [r, g, b] = getPixel(point.x, point.y);
			const hex = toHex(r, g, b);

			updateLoupeAndPreview(point.x, point.y);
			setText("paCoordText", `X: ${point.x}, Y: ${point.y}`);
			syncColorDisplays(hex, r, g, b);
			paSnippetBox.value = generatePointSnippet(point.x, point.y, hex);
		} else {
			const targetX = region.x + (region.w >> 1);
			const targetY = region.y + (region.h >> 1);
			updateLoupeAndPreview(targetX, targetY);
			syncRegionParams();
		}
	}

	// --- Presentation Surface 1: Workspace Interaction ---
	// Drag interaction state (managed via PointerCapture on paViewport — zero global window listeners)
	type DragAction =
		| { type: "pan"; startX: number; startY: number; origPanX: number; origPanY: number }
		| { type: "move"; startX: number; startY: number; origRegion: typeof region }
		| { type: "resize"; handle: string; startX: number; startY: number; origRegion: typeof region }
		| { type: "draw"; startPt: typeof point }
		| null;

	let activeDrag: DragAction = null;

	function onViewportPointerDown(e: PointerEvent) {
		if (!img || !isOverlayOpen || shieldBlocked) return;

		// 1. Pan Start (Middle click or Space held or clicking blank viewport backdrop)
		if (e.button === 1 || isSpaceDown || e.target === paViewport) {
			activeDrag = {
				type: "pan",
				startX: e.clientX,
				startY: e.clientY,
				origPanX: panX,
				origPanY: panY
			};
			paViewport.classList.add("pa-panning");
			paViewport.setPointerCapture(e.pointerId);
			e.preventDefault();
			return;
		}

		if (e.button !== 0) return;

		// 2. Handle Resize Start
		const handleEl = (e.target as HTMLElement | null)?.closest(".pa-handle");
		if (handleEl && mode === "region") {
			const handle = handleEl.getAttribute("data-handle") || "se";
			activeDrag = {
				type: "resize",
				handle,
				startX: e.clientX,
				startY: e.clientY,
				origRegion: { ...region }
			};
			paViewport.setPointerCapture(e.pointerId);
			const p = screenToPixel(e.clientX, e.clientY);
			updateLoupeAndPreview(p.x, p.y);
			e.preventDefault();
			return;
		}

		// 3. Region Move Start
		if (e.target === paSvgBoxBody && mode === "region") {
			activeDrag = {
				type: "move",
				startX: e.clientX,
				startY: e.clientY,
				origRegion: { ...region }
			};
			paViewport.setPointerCapture(e.pointerId);
			const p = screenToPixel(e.clientX, e.clientY);
			updateLoupeAndPreview(p.x, p.y);
			e.preventDefault();
			return;
		}

		// 4. Canvas Click / Region Box Draw
		const p = screenToPixel(e.clientX, e.clientY);
		if (p.inside) {
			if (mode === "point") {
				point = { x: p.x, y: p.y };
				renderSelection();
				renderPinned();
			} else {
				activeDrag = { type: "draw", startPt: { x: p.x, y: p.y } };
				region = { x: p.x, y: p.y, w: 1, h: 1 };
				renderSelection();
				updateLoupeAndPreview(p.x, p.y);
				syncRegionParams();
				paViewport.setPointerCapture(e.pointerId);
				e.preventDefault();
			}
		}
	}

	function onViewportPointerMove(e: PointerEvent) {
		if (activeDrag && img) {
			if (activeDrag.type === "pan") {
				panX = activeDrag.origPanX + (e.clientX - activeDrag.startX);
				panY = activeDrag.origPanY + (e.clientY - activeDrag.startY);
				paStageContainer.style.transform = `translate(${Math.round(panX)}px, ${Math.round(panY)}px)`;
				return;
			}

			if (activeDrag.type === "move") {
				const dx = Math.round((e.clientX - activeDrag.startX) / zoom);
				const dy = Math.round((e.clientY - activeDrag.startY) / zoom);
				const r0 = activeDrag.origRegion;
				region = {
					x: Math.max(0, Math.min(img.width - r0.w, r0.x + dx)),
					y: Math.max(0, Math.min(img.height - r0.h, r0.y + dy)),
					w: r0.w,
					h: r0.h
				};
			} else if (activeDrag.type === "resize") {
				const dx = Math.round((e.clientX - activeDrag.startX) / zoom);
				const dy = Math.round((e.clientY - activeDrag.startY) / zoom);
				const r0 = activeDrag.origRegion;
				let x = r0.x,
					y = r0.y,
					w = r0.w,
					h = r0.h;
				const hnd = activeDrag.handle;
				if (hnd.includes("e")) w = r0.w + dx;
				if (hnd.includes("w")) {
					x = r0.x + dx;
					w = r0.w - dx;
				}
				if (hnd.includes("s")) h = r0.h + dy;
				if (hnd.includes("n")) {
					y = r0.y + dy;
					h = r0.h - dy;
				}
				x = Math.max(0, Math.min(img.width - 1, x));
				y = Math.max(0, Math.min(img.height - 1, y));
				w = Math.max(1, Math.min(128, Math.min(img.width - x, Math.max(1, w))));
				h = Math.max(1, Math.min(128, Math.min(img.height - y, Math.max(1, h))));
				region = { x, y, w, h };
			} else if (activeDrag.type === "draw") {
				const p = screenToPixel(e.clientX, e.clientY);
				const calcSpan = (cur: number, start: number, max: number): [number, number] => {
					if (cur >= start) return [start, Math.max(1, Math.min(128, max - start, cur - start + 1))];
					const span = Math.max(1, Math.min(128, start + 1, start - cur + 1));
					return [start - span + 1, span];
				};
				const [x, w] = calcSpan(p.x, activeDrag.startPt.x, img.width);
				const [y, h] = calcSpan(p.y, activeDrag.startPt.y, img.height);
				region = { x, y, w, h };
			}
			renderSelection();
			const p = screenToPixel(e.clientX, e.clientY);
			updateLoupeAndPreview(p.x, p.y);
			syncRegionParams();
			return;
		}

		// When not dragging, handle hover loupe sampling directly on the workspace
		if (!img || shieldBlocked) return;
		const p = screenToPixel(e.clientX, e.clientY);
		if (p.inside) {
			updateLoupeAndPreview(p.x, p.y);
		} else {
			renderPinned();
		}
	}

	function onViewportPointerUp(e: PointerEvent) {
		if (paViewport.hasPointerCapture(e.pointerId)) {
			paViewport.releasePointerCapture(e.pointerId);
		}
		paViewport.classList.remove("pa-panning");
		if (activeDrag) {
			const was = activeDrag;
			activeDrag = null;
			if (was.type === "move" || was.type === "resize" || was.type === "draw") {
				renderSelection();
				syncRegionParams();
				const p = screenToPixel(e.clientX, e.clientY);
				if (p.inside) {
					updateLoupeAndPreview(p.x, p.y);
				} else {
					renderPinned();
				}
			}
		}
	}

	function onViewportPointerCancel(e: PointerEvent) {
		onViewportPointerUp(e);
	}

	function onViewportPointerLeave() {
		if (activeDrag || !img || shieldBlocked) return;
		renderPinned();
	}

	function onViewportWheel(e: WheelEvent) {
		if (!img || !isOverlayOpen || shieldBlocked) return;
		e.preventDefault();
		if (e.ctrlKey) {
			const factor = Math.exp(-e.deltaY * 0.01);
			applyZoomAt(e.clientX, e.clientY, zoom * factor);
		} else {
			panX -= e.deltaX;
			panY -= e.deltaY;
			renderStage();
		}
	}

	// --- Global Shortcuts & Modal Lifecycle (strictly attached when open, detached when closed) ---
	function flashKeyActive(target: string | HTMLElement | null): void {
		const btn = typeof target === "string" ? $(target) : target;
		if (!btn) return;
		btn.classList.add("pa-key-active");
		setTimeout(() => btn.classList.remove("pa-key-active"), 140);
	}

	function fallbackCopy(text: string): void {
		try {
			const el = document.createElement("textarea");
			el.value = text;
			el.style.cssText = "position:fixed;opacity:0";
			document.body.appendChild(el);
			el.select();
			document.execCommand("copy");
			document.body.removeChild(el);
		} catch {}
	}

	function copyText(text: string): void {
		if (!text) return;
		if (navigator.clipboard?.writeText) {
			navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
		} else {
			fallbackCopy(text);
		}
	}

	function getActivePixel(): [number, number, number] {
		const tx = mode === "point" ? point.x : region.x + (region.w >> 1);
		const ty = mode === "point" ? point.y : region.y + (region.h >> 1);
		return getPixel(tx, ty);
	}

	function getActiveCoords(): string {
		return mode === "point" ? `${point.x},${point.y}` : `${region.x},${region.y},${region.w},${region.h}`;
	}

	function setCaptureStatus(text: string, type: "ok" | "err" | "info"): void {
		const el = $("paCaptureStatus");
		if (!el) return;
		el.textContent = text;
		const [fg, bg, bd] = (type === "ok" ? "#1a7f37;#dafbe1;#4ac26b" : type === "err" ? "#cf222e;#ffebe9;#ff8182" : "#0969da;#ddf4ff;#54aeff").split(";");
		el.style.cssText = `font-size:12px;color:${fg};background:${bg};border:1px solid ${bd};border-radius:4px;padding:2px 8px;`;
	}

	function setCaptureBusy(busy: boolean) {
		isCapturing = busy;
		const b1 = $<HTMLButtonElement>("paCaptureBtn");
		const b2 = $<HTMLButtonElement>("paCapturePromptBtn");
		if (b1) b1.disabled = busy;
		if (b2) b2.disabled = busy;
	}

	function onKeyDown(e: KeyboardEvent) {
		if (!isOverlayOpen || shieldBlocked) return;
		const target = e.target as HTMLElement | null;
		if (target?.matches("input, textarea")) return;

		if (e.key === "Escape") {
			if (paShieldModal.style.display !== "none") {
				paShieldModal.style.display = "none";
			} else {
				closeOverlay();
			}
			return;
		}

		if (e.code === "Space") {
			isSpaceDown = true;
			paViewport.classList.add("pa-pan-ready");
			e.preventDefault();
			return;
		}

		const key = e.key.toLowerCase();
		const copyMap: Record<string, [() => string, string]> = {
			c: [() => "0x" + toHex(...getActivePixel()), "paCopyHexDslBtn"],
			x: [getActiveCoords, "paCopyCoordBtn"],
			s: [() => paSnippetBox.value, "paCopySnippetBtn"]
		};
		if (!e.metaKey && !e.ctrlKey && copyMap[key]) {
			const [fn, btnId] = copyMap[key];
			copyText(fn());
			flashKeyActive(btnId);
			e.preventDefault();
			return;
		}
		if (mode === "region" && img && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
			const step = e.shiftKey ? 10 : 1;
			if (e.key === "ArrowLeft") region.x = Math.max(0, region.x - step);
			if (e.key === "ArrowRight") region.x = Math.min(img.width - region.w, region.x + step);
			if (e.key === "ArrowUp") region.y = Math.max(0, region.y - step);
			if (e.key === "ArrowDown") region.y = Math.min(img.height - region.h, region.y + step);
			e.preventDefault();
			renderSelection();
			renderPinned();
		}
	}

	function onKeyUp(e: KeyboardEvent) {
		if (e.code === "Space") {
			isSpaceDown = false;
			paViewport.classList.remove("pa-pan-ready");
		}
		const keyToBtn: Record<string, string> = { c: "paCopyHexDslBtn", x: "paCopyCoordBtn", s: "paCopySnippetBtn" };
		if (keyToBtn[e.key.toLowerCase()]) $(keyToBtn[e.key.toLowerCase()])?.classList.remove("pa-key-active");
	}

	function onResize() {
		if (isOverlayOpen) {
			updateViewportRect();
			if (paZoomSelect.value === "fit" || paZoomCustomOpt.textContent?.startsWith("Fit")) {
				zoomToFit();
			}
		}
	}

	const vpEvents: [string, any, any?][] = [
		["pointerdown", onViewportPointerDown],
		["pointermove", onViewportPointerMove],
		["pointerup", onViewportPointerUp],
		["pointercancel", onViewportPointerCancel],
		["pointerleave", onViewportPointerLeave],
		["wheel", onViewportWheel, { passive: false }]
	];
	const winEvents: [string, any][] = [
		["keydown", onKeyDown],
		["keyup", onKeyUp],
		["resize", onResize]
	];

	function attachOverlayListeners() {
		for (const [e, fn, opt] of vpEvents) paViewport.addEventListener(e, fn, opt);
		for (const [e, fn] of winEvents) window.addEventListener(e, fn);
	}

	function detachOverlayListeners() {
		for (const [e, fn] of vpEvents) paViewport.removeEventListener(e, fn);
		for (const [e, fn] of winEvents) window.removeEventListener(e, fn);
		activeDrag = null;
		isSpaceDown = false;
		paViewport.classList.remove("pa-pan-ready", "pa-panning");
	}

	// --- Presentation Surface 2: TopBar & Inspector Controls ---
	function openOverlay() {
		isOverlayOpen = true;
		paOverlay.style.display = "flex";
		attachOverlayListeners();
		updateViewportRect();
		if (img) {
			zoomToFit();
			renderSelection();
			renderPinned();
		}
	}

	function closeOverlay() {
		isOverlayOpen = false;
		paOverlay.style.display = "none";
		detachOverlayListeners();
	}

	$("paOpenBtn")?.addEventListener("click", openOverlay);
	$("paCloseBtn")?.addEventListener("click", closeOverlay);

	function setMode(newMode: "point" | "region") {
		mode = newMode;
		const isPoint = mode === "point";
		$("paModePoint")?.classList.toggle("active", isPoint);
		$("paModeRegion")?.classList.toggle("active", !isPoint);
		const badge = $("paHudModeBadge");
		if (badge) badge.textContent = isPoint ? "Point Mode" : "Region Mode";
		const controls = $("paRegionControls");
		if (controls) controls.style.display = isPoint ? "none" : "block";
		renderSelection();
		renderPinned();
	}
	$("paModePoint")?.addEventListener("click", () => setMode("point"));
	$("paModeRegion")?.addEventListener("click", () => setMode("region"));

	$("paGridToggleBtn")?.addEventListener("click", () => {
		showGrid = !showGrid;
		renderPixelGrid();
	});

	const zoomCenter = (z: number) => applyZoomAt(paViewport.clientWidth / 2, paViewport.clientHeight / 2, z);
	$("paZoomInBtn")?.addEventListener("click", () => {
		zoomCenter(ZOOM_PRESETS.find(s => s > zoom * 1.05) ?? Math.min(16, zoom * 1.5));
	});
	$("paZoomOutBtn")?.addEventListener("click", () => {
		zoomCenter([...ZOOM_PRESETS].reverse().find(s => s < zoom * 0.95) ?? Math.max(0.1, zoom / 1.5));
	});
	$("paZoomFitBtn")?.addEventListener("click", zoomToFit);
	paZoomSelect?.addEventListener("change", () => {
		if (paZoomSelect.value === "fit") zoomToFit();
		else zoomCenter(parseFloat(paZoomSelect.value) || 1);
	});

	// Region Manual Numeric Input sync
	[paInX, paInY, paInW, paInH].forEach(input => {
		input?.addEventListener("input", () => {
			if (!img || mode !== "region") return;
			const parseVal = (el: HTMLInputElement, def: number) => parseInt(el.value, 10) || def;
			const rx = Math.max(0, Math.min(img.width - 1, parseVal(paInX, 0)));
			const ry = Math.max(0, Math.min(img.height - 1, parseVal(paInY, 0)));
			const rw = Math.max(1, Math.min(128, img.width - rx, parseVal(paInW, 1)));
			const rh = Math.max(1, Math.min(128, img.height - ry, parseVal(paInH, 1)));
			region = { x: rx, y: ry, w: rw, h: rh };
			renderSelection();
			renderPinned();
		});
	});

	// Copy & Snippet Action Listeners
	function bindCopy(id: string, fn: () => string) {
		$(id)?.addEventListener("click", () => {
			copyText(fn());
			flashKeyActive(id);
		});
	}
	bindCopy("paCopyCoordBtn", getActiveCoords);
	bindCopy("paCopyHexDslBtn", () => "0x" + toHex(...getActivePixel()));
	bindCopy("paCopyHexCssBtn", () => "#" + toHex(...getActivePixel()));
	bindCopy("paCopyRgbBtn", () => `rgb(${getActivePixel().join(", ")})`);
	bindCopy("paCopySnippetBtn", () => paSnippetBox.value);

	$("paInsertSnippetBtn")?.addEventListener("click", () => {
		const snip = paSnippetBox.value;
		if (!snip) return;
		insertTextAtCursor(snip);
		flashKeyActive("paInsertSnippetBtn");
	});

	// --- Presentation Surface 3: Modal Dialog ---
	$("paShieldWarning")?.addEventListener("click", () => {
		paShieldModal.style.display = "flex";
	});

	$("paExitShieldModalBtn")?.addEventListener("click", () => {
		paShieldModal.style.display = "none";
		closeOverlay();
	});

	$("paRetestShieldBtn")?.addEventListener("click", () => {
		const res = $("paShieldModalResult");
		if (!res) return;
		res.style.display = "block";
		if (checkAntiFingerprintNoise()) {
			res.style.background = "#ffebe9";
			res.style.color = "#cf222e";
			res.textContent = "Noise still detected. Please whitelist this origin in Brave/browser shield settings.";
			enforceShield(true);
		} else {
			res.style.background = "#dafbe1";
			res.style.color = "#1a7f37";
			res.textContent = "Canvas is clean. Unlocking workspace...";
			if (img) initPixelBuffer(img);
			setTimeout(() => {
				enforceShield(false);
				renderPinned();
			}, 600);
		}
	});

	// --- Image Capture Engine ---
	function loadCaptureBlob(blob: Blob, sourceLabel: string = "Active capture") {
		const url = URL.createObjectURL(blob);
		const loadedImg = new Image();
		loadedImg.onload = () => {
			img = loadedImg;
			paDisplayCanvas.width = img.width;
			paDisplayCanvas.height = img.height;
			paDisplayCtx.imageSmoothingEnabled = false;
			paDisplayCtx.drawImage(img, 0, 0);
			initPixelBuffer(img);

			point = { x: Math.floor(img.width / 2), y: Math.floor(img.height / 2) };
			region = { x: point.x - 8, y: point.y - 8, w: 16, h: 16 };

			const emptyPrompt = $("paEmptyPrompt");
			if (emptyPrompt) emptyPrompt.style.display = "none";
			paStageContainer.style.display = "inline-block";
			const inspector = $("paInspector");
			if (inspector) inspector.style.display = "flex";
			const resBadge = $("paResBadge");
			if (resBadge) {
				resBadge.style.display = "inline-block";
				resBadge.textContent = `${img.width}×${img.height}`;
			}

			enforceShield(checkAntiFingerprintNoise());
			zoomToFit();
			renderSelection();
			renderPinned();

			setCaptureStatus(`Ready (${img.width}×${img.height})`, "ok");
			const mainStatus = $("paMainStatus");
			if (mainStatus) {
				mainStatus.textContent = `${sourceLabel}: ${img.width}×${img.height}`;
				mainStatus.style.color = "#57606a";
			}
			URL.revokeObjectURL(url);
			setCaptureBusy(false);
		};
		loadedImg.onerror = () => {
			URL.revokeObjectURL(url);
			setCaptureStatus("Failed to decode image", "err");
			setCaptureBusy(false);
		};
		loadedImg.src = url;
	}

	async function triggerCapture() {
		if (isCapturing) return;
		setCaptureBusy(true);
		setCaptureStatus("Requesting screen from capture provider...", "info");

		try {
			const postRes = await fetch("/img/capture", {
				method: "POST",
				headers: headers("application/x-www-form-urlencoded")
			});
			if (!postRes.ok && postRes.status !== 202 && postRes.status !== 204) {
				const txt = await postRes.text().catch(() => "");
				throw new Error(txt || `Capture trigger HTTP ${postRes.status}`);
			}
		} catch (err: any) {
			setCaptureStatus(err.message || "Failed to trigger capture", "err");
			setCaptureBusy(false);
			return;
		}

		const startTime = Date.now();
		const poll = async () => {
			try {
				const res = await fetch("/img/capture", {
					method: "GET",
					headers: headers()
				});
				if (res.status === 200) {
					const data = await res.json();
					setCaptureStatus("Fetching direct frame from OBS...", "info");
					const token = getToken();
					const sig = hmacSha256Hex(token, data.nonce);
					const directUrl = `${data.url}?type=png&nonce=${encodeURIComponent(data.nonce)}&sig=${encodeURIComponent(sig)}`;
					let directRes: Response;
					try {
						directRes = await fetch(directUrl);
					} catch (fetchErr: any) {
						throw new Error(
							"LAN direct transfer blocked by browser/ad-blocker (ERR_BLOCKED_BY_CLIENT). Please whitelist this page in your ad blocker or test in Incognito mode."
						);
					}
					if (!directRes.ok) {
						throw new Error(`OBS direct transfer rejected (HTTP ${directRes.status})`);
					}
					const blob = await directRes.blob();
					loadCaptureBlob(blob, "OBS Direct Capture");
					return;
				}
				if (res.status !== 204) {
					const msg = await res.text().catch(() => "");
					throw new Error(msg ? `Device capture error: ${msg}` : `Capture failed (HTTP ${res.status})`);
				}
				if (Date.now() - startTime > 10000) {
					throw new Error("Capture timed out (no capture provider responded)");
				}
				setTimeout(poll, 300);
			} catch (err: any) {
				setCaptureStatus(err.message || "Capture poll failed", "err");
				setCaptureBusy(false);
			}
		};
		setTimeout(poll, 300);
	}

	$("paCaptureBtn")?.addEventListener("click", triggerCapture);
	$("paCapturePromptBtn")?.addEventListener("click", triggerCapture);
}
