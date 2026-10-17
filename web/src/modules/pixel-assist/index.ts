import { headers } from "../../utils/api";
import { generatePointSnippet, generateRegionSnippet, toHex } from "./generator";

export function initPixelAssist(): void {
	// --- DOM Element References ---
	const paOverlay = document.getElementById("paOverlay")!;
	const paViewport = document.getElementById("paViewport")!;
	const paInspector = document.getElementById("paInspector")!;
	const paShieldModal = document.getElementById("paShieldModal")!;
	const paShieldWarning = document.getElementById("paShieldWarning") as HTMLButtonElement | null;
	const paShieldModalResult = document.getElementById("paShieldModalResult");
	const paExitShieldModalBtn = document.getElementById("paExitShieldModalBtn");
	const paRetestShieldBtn = document.getElementById("paRetestShieldBtn");

	const paOpenBtn = document.getElementById("paOpenBtn");
	const paCloseBtn = document.getElementById("paCloseBtn");
	const paCaptureBtn = document.getElementById("paCaptureBtn") as HTMLButtonElement | null;
	const paCapturePromptBtn = document.getElementById("paCapturePromptBtn") as HTMLButtonElement | null;
	const paCaptureStatus = document.getElementById("paCaptureStatus");
	const paMainStatus = document.getElementById("paMainStatus");
	const paResBadge = document.getElementById("paResBadge");
	const paEmptyPrompt = document.getElementById("paEmptyPrompt");

	const paStageContainer = document.getElementById("paStageContainer")!;
	const paDisplayCanvas = document.getElementById("paDisplayCanvas") as HTMLCanvasElement;
	const paDisplayCtx = paDisplayCanvas.getContext("2d")!;
	const paOverlaySvg = document.getElementById("paOverlaySvg") as unknown as SVGElement;
	const paPixelGridRect = document.getElementById("paPixelGridRect") as unknown as SVGRectElement;
	const paPixelGridPattern = document.getElementById("paPixelGridPattern") as unknown as SVGPatternElement;
	const paPixelGridPath = document.getElementById("paPixelGridPath") as unknown as SVGPathElement;
	const paSvgSelectionGroup = document.getElementById("paSvgSelectionGroup") as unknown as SVGElement;
	const paSvgBox = document.getElementById("paSvgBox") as unknown as SVGRectElement;
	const paSvgBoxOuter = document.getElementById("paSvgBoxOuter") as unknown as SVGRectElement;
	const paSvgBoxBody = document.getElementById("paSvgBoxBody") as unknown as SVGRectElement;
	const paSvgHandles = document.getElementById("paSvgHandles") as unknown as SVGElement;

	const paModePoint = document.getElementById("paModePoint");
	const paModeRegion = document.getElementById("paModeRegion");
	const paHudModeBadge = document.getElementById("paHudModeBadge");
	const paRegionControls = document.getElementById("paRegionControls");
	const paGridToggleBtn = document.getElementById("paGridToggleBtn") as HTMLButtonElement | null;
	const paZoomSelect = document.getElementById("paZoomSelect") as HTMLSelectElement;
	const paZoomCustomOpt = document.getElementById("paZoomCustomOpt") as HTMLOptionElement;
	const paZoomInBtn = document.getElementById("paZoomInBtn");
	const paZoomOutBtn = document.getElementById("paZoomOutBtn");
	const paZoomFitBtn = document.getElementById("paZoomFitBtn");

	const paLoupeCanvas = document.getElementById("paLoupeCanvas") as HTMLCanvasElement;
	const paLoupeCtx = paLoupeCanvas.getContext("2d")!;
	paLoupeCtx.imageSmoothingEnabled = false;

	const paColorSwatch = document.getElementById("paColorSwatch");
	const paColorHexShort = document.getElementById("paColorHexShort");
	const paCoordText = document.getElementById("paCoordText");
	const paHexDslText = document.getElementById("paHexDslText");
	const paHexCssText = document.getElementById("paHexCssText");
	const paRgbText = document.getElementById("paRgbText");
	const paRegionSwatches = document.getElementById("paRegionSwatches");
	const paSnippetBox = document.getElementById("paSnippetBox") as HTMLTextAreaElement;
	const paInsertSnippetBtn = document.getElementById("paInsertSnippetBtn");
	const paCopyCoordBtn = document.getElementById("paCopyCoordBtn");
	const paCopyHexDslBtn = document.getElementById("paCopyHexDslBtn");
	const paCopyHexCssBtn = document.getElementById("paCopyHexCssBtn");
	const paCopyRgbBtn = document.getElementById("paCopyRgbBtn");
	const paCopySnippetBtn = document.getElementById("paCopySnippetBtn");
	const macroCodeEl = document.getElementById("macroCode") as HTMLTextAreaElement | null;

	const paInX = document.getElementById("paInX") as HTMLInputElement;
	const paInY = document.getElementById("paInY") as HTMLInputElement;
	const paInW = document.getElementById("paInW") as HTMLInputElement;
	const paInH = document.getElementById("paInH") as HTMLInputElement;

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
		if (paShieldWarning) paShieldWarning.style.display = locked ? "inline-flex" : "none";
		if (paShieldModalResult) {
			paShieldModalResult.style.display = locked ? "block" : "none";
			if (locked) {
				paShieldModalResult.style.background = "#ffebe9";
				paShieldModalResult.style.color = "#cf222e";
				paShieldModalResult.textContent = "Color sampling is disabled until browser canvas noise protection is disabled.";
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
		const presets = [0.25, 0.5, 1, 2, 4, 8, 16];
		const exact = !isFit && presets.find(z => Math.round(z * 100) === pct);
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
		if (paGridToggleBtn) {
			paGridToggleBtn.style.color = showGrid ? "#0969da" : "#57606a";
			paGridToggleBtn.style.borderColor = showGrid ? "#0969da" : "#d0d7de";
			paGridToggleBtn.style.background = showGrid ? "#f0f7ff" : "#ffffff";
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

		paSvgBox.setAttribute("x", String(sx));
		paSvgBox.setAttribute("y", String(sy));
		paSvgBox.setAttribute("width", String(sw));
		paSvgBox.setAttribute("height", String(sh));

		paSvgBoxOuter.setAttribute("x", String(sx - 1));
		paSvgBoxOuter.setAttribute("y", String(sy - 1));
		paSvgBoxOuter.setAttribute("width", String(sw + 2));
		paSvgBoxOuter.setAttribute("height", String(sh + 2));

		paSvgBoxBody.setAttribute("x", String(sx));
		paSvgBoxBody.setAttribute("y", String(sy));
		paSvgBoxBody.setAttribute("width", String(sw));
		paSvgBoxBody.setAttribute("height", String(sh));
		paSvgBoxBody.style.pointerEvents = isPoint ? "none" : "all";

		paSvgHandles.style.display = isPoint ? "none" : "block";
		if (!isPoint) {
			const hs = 7;
			const half = hs / 2;
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
			paSvgHandles.querySelectorAll(".pa-handle").forEach(el => {
				const k = el.getAttribute("data-handle");
				if (k && posMap[k]) {
					el.setAttribute("x", String(posMap[k][0] - half));
					el.setAttribute("y", String(posMap[k][1] - half));
				}
			});
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
		if (paColorSwatch) paColorSwatch.style.backgroundColor = `rgb(${r},${g},${b})`;
		if (paColorHexShort) paColorHexShort.textContent = `0x${hex}`;
	}

	function syncRegionParams() {
		if (!img) return;
		paInX.value = String(region.x);
		paInY.value = String(region.y);
		paInW.value = String(region.w);
		paInH.value = String(region.h);

		if (paCoordText) {
			paCoordText.textContent = `Rect: ${region.x},${region.y} (${region.w}×${region.h})`;
		}

		const targetX = region.x + (region.w >> 1);
		const targetY = region.y + (region.h >> 1);
		const [r, g, b] = getPixel(targetX, targetY);
		const hex = toHex(r, g, b);

		if (paHexDslText) paHexDslText.textContent = `0x${hex}`;
		if (paHexCssText) paHexCssText.textContent = `#${hex}`;
		if (paRgbText) paRgbText.textContent = `rgb(${r}, ${g}, ${b})`;

		if (paRegionSwatches) {
			paRegionSwatches.innerHTML = "";
			const pts = [
				{ name: "TL", x: region.x, y: region.y },
				{ name: "TR", x: region.x + region.w - 1, y: region.y },
				{ name: "BL", x: region.x, y: region.y + region.h - 1 },
				{ name: "BR", x: region.x + region.w - 1, y: region.y + region.h - 1 },
				{ name: "Center", x: targetX, y: targetY }
			];
			pts.forEach(pt => {
				const [pr, pg, pb] = getPixel(pt.x, pt.y);
				const phex = toHex(pr, pg, pb);
				const badge = document.createElement("span");
				badge.style.cssText =
					"display:inline-flex;align-items:center;gap:3px;background:#f6f8fa;padding:2px 5px;border-radius:3px;font-size:10px;font-family:monospace;border:1px solid #d0d7de;";
				badge.title = `${pt.name}: (${pt.x},${pt.y}) 0x${phex}`;
				badge.innerHTML = `<span style="width:9px;height:9px;border-radius:2px;background:rgb(${pr},${pg},${pb});display:inline-block;border:1px solid #d0d7de"></span> ${pt.name}: 0x${phex}`;
				paRegionSwatches.appendChild(badge);
			});
		}

		paSnippetBox.value = generateRegionSnippet(region.x, region.y, region.w, region.h, getPixel);
	}

	function renderPinned() {
		if (!img) return;
		const isPoint = mode === "point";
		if (isPoint) {
			const [r, g, b] = getPixel(point.x, point.y);
			const hex = toHex(r, g, b);

			renderLoupe(point.x, point.y);
			updateHoverPreview(point.x, point.y);

			if (paCoordText) paCoordText.textContent = `X: ${point.x}, Y: ${point.y}`;
			if (paHexDslText) paHexDslText.textContent = `0x${hex}`;
			if (paHexCssText) paHexCssText.textContent = `#${hex}`;
			if (paRgbText) paRgbText.textContent = `rgb(${r}, ${g}, ${b})`;
			paSnippetBox.value = generatePointSnippet(point.x, point.y, hex);
		} else {
			const targetX = region.x + (region.w >> 1);
			const targetY = region.y + (region.h >> 1);
			renderLoupe(targetX, targetY);
			updateHoverPreview(targetX, targetY);
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
			renderLoupe(p.x, p.y);
			updateHoverPreview(p.x, p.y);
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
			renderLoupe(p.x, p.y);
			updateHoverPreview(p.x, p.y);
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
				renderLoupe(p.x, p.y);
				updateHoverPreview(p.x, p.y);
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
				renderSelection();
				const p = screenToPixel(e.clientX, e.clientY);
				renderLoupe(p.x, p.y);
				updateHoverPreview(p.x, p.y);
				syncRegionParams();
				return;
			}

			if (activeDrag.type === "resize") {
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
				renderSelection();
				const p = screenToPixel(e.clientX, e.clientY);
				renderLoupe(p.x, p.y);
				updateHoverPreview(p.x, p.y);
				syncRegionParams();
				return;
			}

			if (activeDrag.type === "draw") {
				const p = screenToPixel(e.clientX, e.clientY);
				let x0 = activeDrag.startPt.x;
				let y0 = activeDrag.startPt.y;
				let w = 1;
				let h = 1;
				if (p.x >= activeDrag.startPt.x) {
					w = Math.max(1, Math.min(128, Math.min(img.width - x0, p.x - x0 + 1)));
				} else {
					const rawW = activeDrag.startPt.x - p.x + 1;
					w = Math.max(1, Math.min(128, Math.min(activeDrag.startPt.x + 1, rawW)));
					x0 = activeDrag.startPt.x - w + 1;
				}
				if (p.y >= activeDrag.startPt.y) {
					h = Math.max(1, Math.min(128, Math.min(img.height - y0, p.y - y0 + 1)));
				} else {
					const rawH = activeDrag.startPt.y - p.y + 1;
					h = Math.max(1, Math.min(128, Math.min(activeDrag.startPt.y + 1, rawH)));
					y0 = activeDrag.startPt.y - h + 1;
				}
				region = { x: x0, y: y0, w, h };
				renderSelection();
				renderLoupe(p.x, p.y);
				updateHoverPreview(p.x, p.y);
				syncRegionParams();
				return;
			}
			return;
		}

		// When not dragging, handle hover loupe sampling directly on the workspace
		if (!img || shieldBlocked) return;
		const p = screenToPixel(e.clientX, e.clientY);
		if (p.inside) {
			renderLoupe(p.x, p.y);
			updateHoverPreview(p.x, p.y);
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
					renderLoupe(p.x, p.y);
					updateHoverPreview(p.x, p.y);
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
	function flashKeyActive(btn: HTMLElement | null): void {
		if (!btn) return;
		btn.classList.add("pa-key-active");
		setTimeout(() => btn.classList.remove("pa-key-active"), 140);
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
		if (key === "c" && !e.metaKey && !e.ctrlKey) {
			const targetX = mode === "point" ? point.x : region.x + (region.w >> 1);
			const targetY = mode === "point" ? point.y : region.y + (region.h >> 1);
			const [r, g, b] = getPixel(targetX, targetY);
			navigator.clipboard.writeText("0x" + toHex(r, g, b)).catch(() => {});
			flashKeyActive(paCopyHexDslBtn);
			e.preventDefault();
		} else if (key === "x" && !e.metaKey && !e.ctrlKey) {
			const coords = mode === "point" ? `${point.x},${point.y}` : `${region.x},${region.y},${region.w},${region.h}`;
			navigator.clipboard.writeText(coords).catch(() => {});
			flashKeyActive(paCopyCoordBtn);
			e.preventDefault();
		} else if (key === "s" && !e.metaKey && !e.ctrlKey) {
			navigator.clipboard.writeText(paSnippetBox.value).catch(() => {});
			flashKeyActive(paCopySnippetBtn);
			e.preventDefault();
		} else if (mode === "region" && img && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
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
		const key = e.key.toLowerCase();
		if (key === "c") paCopyHexDslBtn?.classList.remove("pa-key-active");
		if (key === "x") paCopyCoordBtn?.classList.remove("pa-key-active");
		if (key === "s") paCopySnippetBtn?.classList.remove("pa-key-active");
	}

	function onResize() {
		if (isOverlayOpen) {
			updateViewportRect();
			if (paZoomSelect.value === "fit" || paZoomCustomOpt.textContent?.startsWith("Fit")) {
				zoomToFit();
			}
		}
	}

	function attachOverlayListeners() {
		window.addEventListener("keydown", onKeyDown);
		window.addEventListener("keyup", onKeyUp);
		window.addEventListener("resize", onResize);

		paViewport.addEventListener("pointerdown", onViewportPointerDown);
		paViewport.addEventListener("pointermove", onViewportPointerMove);
		paViewport.addEventListener("pointerup", onViewportPointerUp);
		paViewport.addEventListener("pointercancel", onViewportPointerCancel);
		paViewport.addEventListener("pointerleave", onViewportPointerLeave);
		paViewport.addEventListener("wheel", onViewportWheel, { passive: false });
	}

	function detachOverlayListeners() {
		window.removeEventListener("keydown", onKeyDown);
		window.removeEventListener("keyup", onKeyUp);
		window.removeEventListener("resize", onResize);

		paViewport.removeEventListener("pointerdown", onViewportPointerDown);
		paViewport.removeEventListener("pointermove", onViewportPointerMove);
		paViewport.removeEventListener("pointerup", onViewportPointerUp);
		paViewport.removeEventListener("pointercancel", onViewportPointerCancel);
		paViewport.removeEventListener("pointerleave", onViewportPointerLeave);
		paViewport.removeEventListener("wheel", onViewportWheel);

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

	paOpenBtn?.addEventListener("click", openOverlay);
	paCloseBtn?.addEventListener("click", closeOverlay);

	function setMode(newMode: "point" | "region") {
		mode = newMode;
		const isPoint = mode === "point";
		paModePoint?.classList.toggle("active", isPoint);
		paModeRegion?.classList.toggle("active", !isPoint);
		if (paHudModeBadge) paHudModeBadge.textContent = isPoint ? "Point Mode" : "Region Mode";
		if (paRegionControls) paRegionControls.style.display = isPoint ? "none" : "block";
		renderSelection();
		renderPinned();
	}
	paModePoint?.addEventListener("click", () => setMode("point"));
	paModeRegion?.addEventListener("click", () => setMode("region"));

	paGridToggleBtn?.addEventListener("click", () => {
		showGrid = !showGrid;
		renderPixelGrid();
	});

	paZoomInBtn?.addEventListener("click", () => {
		const steps = [0.25, 0.5, 1, 2, 4, 8, 16];
		const next = steps.find(s => s > zoom * 1.05) ?? Math.min(16, zoom * 1.5);
		applyZoomAt(paViewport.clientWidth / 2, paViewport.clientHeight / 2, next);
	});

	paZoomOutBtn?.addEventListener("click", () => {
		const steps = [0.25, 0.5, 1, 2, 4, 8, 16];
		const prev = [...steps].reverse().find(s => s < zoom * 0.95) ?? Math.max(0.1, zoom / 1.5);
		applyZoomAt(paViewport.clientWidth / 2, paViewport.clientHeight / 2, prev);
	});

	paZoomFitBtn?.addEventListener("click", zoomToFit);

	paZoomSelect?.addEventListener("change", () => {
		if (paZoomSelect.value === "fit") {
			zoomToFit();
		} else {
			const z = parseFloat(paZoomSelect.value) || 1;
			applyZoomAt(paViewport.clientWidth / 2, paViewport.clientHeight / 2, z);
		}
	});


	// Region Manual Numeric Input sync
	[paInX, paInY, paInW, paInH].forEach(input => {
		input?.addEventListener("input", () => {
			if (!img || mode !== "region") return;
			const rx = Math.max(0, Math.min(img.width - 1, parseInt(paInX.value, 10) || 0));
			const ry = Math.max(0, Math.min(img.height - 1, parseInt(paInY.value, 10) || 0));
			const rw = Math.max(1, Math.min(128, Math.min(img.width - rx, parseInt(paInW.value, 10) || 1)));
			const rh = Math.max(1, Math.min(128, Math.min(img.height - ry, parseInt(paInH.value, 10) || 1)));
			region = { x: rx, y: ry, w: rw, h: rh };
			renderSelection();
			renderPinned();
		});
	});

	// Copy & Snippet Action Listeners
	paCopyCoordBtn?.addEventListener("click", () => {
		const coords = mode === "point" ? `${point.x},${point.y}` : `${region.x},${region.y},${region.w},${region.h}`;
		navigator.clipboard.writeText(coords).catch(() => {});
	});
	paCopyHexDslBtn?.addEventListener("click", () => {
		const targetX = mode === "point" ? point.x : region.x + (region.w >> 1);
		const targetY = mode === "point" ? point.y : region.y + (region.h >> 1);
		const [r, g, b] = getPixel(targetX, targetY);
		navigator.clipboard.writeText("0x" + toHex(r, g, b)).catch(() => {});
	});
	paCopyHexCssBtn?.addEventListener("click", () => {
		const targetX = mode === "point" ? point.x : region.x + (region.w >> 1);
		const targetY = mode === "point" ? point.y : region.y + (region.h >> 1);
		const [r, g, b] = getPixel(targetX, targetY);
		navigator.clipboard.writeText("#" + toHex(r, g, b)).catch(() => {});
	});
	paCopyRgbBtn?.addEventListener("click", () => {
		const targetX = mode === "point" ? point.x : region.x + (region.w >> 1);
		const targetY = mode === "point" ? point.y : region.y + (region.h >> 1);
		const [r, g, b] = getPixel(targetX, targetY);
		navigator.clipboard.writeText(`rgb(${r}, ${g}, ${b})`).catch(() => {});
	});
	paCopySnippetBtn?.addEventListener("click", () => {
		navigator.clipboard.writeText(paSnippetBox.value).catch(() => {});
	});

	paInsertSnippetBtn?.addEventListener("click", () => {
		const snip = paSnippetBox.value;
		if (!snip || !macroCodeEl) return;
		macroCodeEl.value += (macroCodeEl.value && !macroCodeEl.value.endsWith("\n") ? "\n" : "") + snip + "\n";
	});

	// --- Presentation Surface 3: Modal Dialog ---
	paShieldWarning?.addEventListener("click", () => {
		paShieldModal.style.display = "flex";
	});

	paExitShieldModalBtn?.addEventListener("click", () => {
		paShieldModal.style.display = "none";
		closeOverlay();
	});

	paRetestShieldBtn?.addEventListener("click", () => {
		if (!paShieldModalResult) return;
		paShieldModalResult.style.display = "block";
		if (checkAntiFingerprintNoise()) {
			paShieldModalResult.style.background = "#ffebe9";
			paShieldModalResult.style.color = "#cf222e";
			paShieldModalResult.textContent = "Noise still detected. Please whitelist this origin in Brave/browser shield settings.";
			enforceShield(true);
		} else {
			paShieldModalResult.style.background = "#dafbe1";
			paShieldModalResult.style.color = "#1a7f37";
			paShieldModalResult.textContent = "Canvas is clean. Unlocking workspace...";
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

			if (paEmptyPrompt) paEmptyPrompt.style.display = "none";
			paStageContainer.style.display = "inline-block";
			paInspector.style.display = "flex";
			if (paResBadge) {
				paResBadge.style.display = "inline-block";
				paResBadge.textContent = `${img.width}×${img.height}`;
			}

			enforceShield(checkAntiFingerprintNoise());
			zoomToFit();
			renderSelection();
			renderPinned();

			if (paCaptureStatus) {
				paCaptureStatus.textContent = `Ready (${img.width}×${img.height})`;
				paCaptureStatus.style.cssText =
					"font-size:12px;color:#1a7f37;background:#dafbe1;border:1px solid #4ac26b;border-radius:4px;padding:2px 8px;";
			}
			if (paMainStatus) {
				paMainStatus.textContent = `${sourceLabel}: ${img.width}×${img.height}`;
				paMainStatus.style.color = "#57606a";
			}
			URL.revokeObjectURL(url);
			if (paCaptureBtn) paCaptureBtn.disabled = false;
			if (paCapturePromptBtn) paCapturePromptBtn.disabled = false;
			isCapturing = false;
		};
		loadedImg.onerror = () => {
			URL.revokeObjectURL(url);
			if (paCaptureStatus) {
				paCaptureStatus.textContent = "Failed to decode image";
				paCaptureStatus.style.cssText =
					"font-size:12px;color:#cf222e;background:#ffebe9;border:1px solid #ff8182;border-radius:4px;padding:2px 8px;";
			}
			if (paCaptureBtn) paCaptureBtn.disabled = false;
			if (paCapturePromptBtn) paCapturePromptBtn.disabled = false;
			isCapturing = false;
		};
		loadedImg.src = url;
	}

	async function triggerCapture() {
		if (isCapturing) return;
		isCapturing = true;
		if (paCaptureBtn) paCaptureBtn.disabled = true;
		if (paCapturePromptBtn) paCapturePromptBtn.disabled = true;
		if (paCaptureStatus) {
			paCaptureStatus.textContent = "Requesting screen from capture provider...";
			paCaptureStatus.style.cssText =
				"font-size:12px;color:#0969da;background:#ddf4ff;border:1px solid #54aeff;border-radius:4px;padding:2px 8px;";
		}

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
			if (paCaptureStatus) {
				paCaptureStatus.textContent = err.message || "Failed to trigger capture";
				paCaptureStatus.style.cssText =
					"font-size:12px;color:#cf222e;background:#ffebe9;border:1px solid #ff8182;border-radius:4px;padding:2px 8px;";
			}
			if (paCaptureBtn) paCaptureBtn.disabled = false;
			if (paCapturePromptBtn) paCapturePromptBtn.disabled = false;
			isCapturing = false;
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
					const blob = await res.blob();
					loadCaptureBlob(blob, "OBS capture");
					return;
				}
				if (Date.now() - startTime > 10000) {
					throw new Error("Capture timed out (no capture provider responded)");
				}
				setTimeout(poll, 300);
			} catch (err: any) {
				if (paCaptureStatus) {
					paCaptureStatus.textContent = err.message || "Capture poll failed";
					paCaptureStatus.style.cssText =
						"font-size:12px;color:#cf222e;background:#ffebe9;border:1px solid #ff8182;border-radius:4px;padding:2px 8px;";
				}
				if (paCaptureBtn) paCaptureBtn.disabled = false;
				if (paCapturePromptBtn) paCapturePromptBtn.disabled = false;
				isCapturing = false;
			}
		};
		setTimeout(poll, 300);
	}

	paCaptureBtn?.addEventListener("click", triggerCapture);
	paCapturePromptBtn?.addEventListener("click", triggerCapture);
}
