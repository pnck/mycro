# Pixel Assist: Interaction Contract

## 1. Purpose and Scope

Pixel Assist is a full-screen authoring surface for inspecting a native-resolution OBS screenshot, selecting point/region coordinates and RGB values, and generating Macro DSL v4 snippets. It is an inspection and authoring tool, not a template-matching or macro-debugging interface.

This document records current UI behavior. Preview transport and authentication are specified in `docs/protocol.md`; DSL and image-resource semantics are specified in `spec/dsl.md`, `spec/runtime.md`, and `spec/vm.md`. The main implementation is `web/src/modules/pixel-assist/index.ts`, with pure snippet generation in `generator.ts` and styling in `web/src/styles/pixel-assist.css`.

## 2. Overlay and Capture Lifecycle

### 2.1 Opening and retained state

**Launch Pixel Assist** opens a fixed, full-window overlay (`z-index: 9999`) without automatically requesting a frame. Its 48px toolbar contains close/download actions, capture status, Point/Region selection, zoom/Fit controls, and Grid.

Before the first frame, a centered prompt offers **Capture Screen Now** and **Download OBS Script**. A successful load hides the prompt, shows the stage/inspector, and displays native dimensions.

Closing through **Close (Esc)** hides the overlay and detaches its interaction listeners. The frame, targets, mode, and grid preference remain in memory; reopening rerenders them and applies Fit again. Nothing is persisted across page reloads. Closing does not cancel an in-flight capture, which may still finish and update the retained frame and main-page status.

### 2.2 Floating inspector and empty stage

The inspector floats over the canvas at `right: 20px; top: 60px; bottom: 16px; width: 330px`; it never becomes a sidebar that shrinks the capture. It scrolls independently when content exceeds the viewport. The canvas remains underneath it and uses the remaining space below the toolbar.

### 2.3 Capture feedback

Both capture buttons share one flow and are disabled while it is active:

1. `POST /img/capture` asks the device to arm a preview.
2. The browser polls every 300ms. Device-side pending responses keep the **Requesting screen from capture provider...** state; no ready ticket after 10 seconds produces a timeout error.
3. A ready ticket changes the status to **Fetching direct frame from OBS...**, and the browser fetches the signed PNG URL directly from OBS.
4. Successful decode shows **Ready (W×H)**. Device, network, signature, and image-decode failures appear in the capture status and re-enable capture.

The status describes this workflow, not continuous provider health. The direct OBS fetch has no separate timeout. Full-frame bytes do not pass through device snapshot buffers; ticket format, HMAC, CORS/PNA, and the short-lived single-frame server are transport concerns defined in `docs/protocol.md`.

## 3. Canvas Navigation

### 3.1 Zoom and Fit

- Presets are Fit, 25%, 50%, 100%, 200%, 400%, 800%, and 1600%; other values appear as a dynamic percentage.
- `−` / `+` normally move between presets, then use 1.5× fallback steps, centered in the viewport.
- `Ctrl + wheel` (including browser pinch gestures reported this way) applies exponential zoom, clamped to 10%–3200%.
- Ordinary wheel/trackpad input **pans** by `deltaX`/`deltaY`; it does not zoom.
- Fit centers the frame with 48px margins and caps its scale at 100%. Large images can fit below 10%.
- Window resize reapplies Fit only while Fit is the selected/displayed mode. Reopening a retained frame also applies Fit.

The current Ctrl+wheel implementation passes client coordinates directly into viewport-local focal math, so the toolbar offset can affect the apparent zoom anchor.

### 3.2 Pointer gestures

| Input | Behavior |
|---|---|
| Left click on image in Point mode | Immediately pins that native pixel |
| Left drag on unoccupied image in Region mode | Draws/normalizes a region, including reverse-direction drags |
| Drag region body / one of eight handles | Moves / resizes the region |
| Middle-button drag | Pans |
| Space-held pointer drag | Pans |
| Drag on the empty viewport backdrop | Pans |
| Ordinary left drag on the image without Space | Never becomes backdrop panning |

Active drags use pointer capture and clean up on release/cancellation. Cursor affordances are crosshair, move, directional resize, grab while Space is held, and grabbing during a pan.

All target coordinates are native integer pixels. Pointer movement during region manipulation converts screen deltas with rounding; interactive edits clamp the region to the image and to `1..128` pixels per edge.

### 3.3 Pixel grid

The grid preference defaults to on for the session, but the SVG grid is drawn only at `zoom >= 3.9` (approximately 400%). **Grid** toggles that preference; turning it off suppresses the grid even at high zoom. The preference survives close/reopen and new captures, but not reloads.

## 4. Point and Region Inspection

### 4.1 Targets and pinned versus hover values

Point and Region modes retain separate targets when switching. A new capture resets the point to `floor(W/2), floor(H/2)` and the region to a nominal centered 16×16 box starting at `point - 8`, without changing the current mode or grid preference. This initial region assignment does not run the interactive clamp and assumes a normal desktop-sized frame.

Hovering the image updates only the loupe and large hover swatch. Labelled coordinate/color readouts and the snippet remain pinned to the selected point or region. Leaving the image/viewport restores the pinned loupe and swatch.

### 4.2 Loupe and colors

The loupe shows an 11×11 native-pixel neighborhood as 12×12 cells on a 132×132 canvas, with a two-layer center reticle and white out-of-image cells.

The active pinned color is the point in Point mode, or `x + floor(w/2), y + floor(h/2)` in Region mode. Readouts use uppercase `0xRRGGBB`, `#RRGGBB`, and `rgb(R, G, B)`.

Region mode additionally shows:

- Four two-way numeric inputs (`X`, `Y`, `W`, `H`), applied and clamped on every input event.
- Top-left, top-right, bottom-left, bottom-right, and center swatches with coordinates/colors in their tooltips. Corners use inclusive `x+w-1` / `y+h-1`; samples can coincide in small regions.

### 4.3 Keyboard movement

With a frame in Region mode, arrows move by 1 native pixel and `Shift + Arrow` moves by 10, clamped to the image. There is no keyboard resize binding. These shortcuts are ignored while an input or textarea has focus.

## 5. Copy, Insert, and Global Shortcuts

### 5.1 Copy actions

| Button / shortcut | Copied value |
|---|---|
| Coordinate / `X` | Point `x,y`; Region `x,y,w,h` |
| DSL hex / `C` | Active pinned `0xRRGGBB` |
| CSS hex | Active pinned `#RRGGBB` |
| RGB | Active pinned `rgb(R, G, B)` |
| Snippet / `S` | Current snippet textarea contents, including manual edits |

Copy uses the asynchronous Clipboard API with a legacy textarea fallback. Fallback failure is silent; button feedback is not proof that the clipboard write succeeded. Copy/keyboard actions apply a 140ms pressed style, also removed on keyup. `X`, `C`, and `S` require no Ctrl/Cmd modifier and are ignored in inputs/textareas.

### 5.2 Escape and focus boundaries

`Escape` closes Pixel Assist only when the overlay is open, the event target is not an input/textarea, and shield blocking is inactive. In particular:

- Escape in a bounds input or the snippet textarea does nothing to the overlay.
- While anti-fingerprint shield blocking is active, the global key handler returns before its Escape branch. The modal must be handled through **Exit Pixel Assist** or **Re-test & Unlock**; there is no Escape-based modal dismissal while the lock is active.

**Insert into Editor** leaves the overlay open but focuses the underlying editor textarea. Until focus returns to a non-input overlay element, Pixel Assist keyboard shortcuts are consequently suppressed.

## 6. Snippet Studio

The textarea is editable and vertically resizable (110px default, 70–220px range); Insert/Copy use its current value. It is not a durable draft: target/mode changes, numeric edits, new captures, and other pinned-render paths regenerate and overwrite it. Generated snippets use fixed variable names and contain no variable-collision avoidance or explicit `$err` recovery.

| Target | Generated behavior |
|---|---|
| Point | Fresh `img.snap` at native `x,y,1,1`; `img.at` at local `0,0`; exact scalar comparison with the pinned color |
| Region with `w <= 16` **and** `h <= 16` | Exact cached-pixel `\pixelrect` matrix; fresh `img.snap`; `img.diff` with tolerance **16**; branch on zero differing pixels |
| Region with either edge greater than 16 | Snapshot; comment placeholder plus fixed `\delay{200ms}`; second snapshot; `img.diff` with tolerance **16**; branch on unchanged/changed |

The threshold tests both edges independently; it is not an area-only rule. An exact reference matrix does not imply exact comparison: tolerance 16 means a pixel differs only when its maximum absolute RGB-channel difference exceeds 16. The large-region template does not perform the commented action itself.

Insert replaces the editor selection/cursor range through the shared custom undo history and does not use the clipboard. Copy Snippet uses the copy path above.

## 7. Anti-Fingerprint Lock

The heuristic runs after each successful image load, not on overlay mount/open. It probes a separate synthetic canvas for exact and repeated readback; it does not compare the OBS source with decoded screenshot pixels, and probe exceptions fail open.

When noise is detected, the warning and blocking dialog appear, and viewport targeting, hover sampling, wheel handling, and global shortcuts stop. The warning badge can reopen the dialog. The dialog offers:

- **Exit Pixel Assist**: close the overlay.
- **Re-test & Unlock**: repeat the probe; on success rebuild the cached image pixels and unlock after 600ms, otherwise remain locked.

This is a fail-open heuristic, not a guarantee of end-to-end pixel integrity.
