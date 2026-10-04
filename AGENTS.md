# AGENTS.md — MYCRO Cold-Start Manual

A USB HID keyboard/mouse macro device based on CircuitPython: an ESP32-S3 emulates keyboard and mouse over USB while serving WiFi HTTP; users write LaTeX-style macro scripts, which the device **compiles to custom bytecode** and runs on a built-in **stack VM**.

See [ROADMAP.md](ROADMAP.md) for the evolution plan. **Architectural discipline: the compiler/VM/protocol layers stay runtime-agnostic; all hardware interaction goes through the HIDProvider abstraction.**

## 1. Files & Deployment Layout

| Repo file | Device path | Role |
|---|---|---|
| `code.py` | `/code.py` | Entry point (auto-run by CircuitPython convention): HID + WiFi HTTP service |
| `macro.py` | **`/lib/macro.py`** ⚠️ | Macro compiler + VM; `code.py` does `from lib.macro import Macro`, so deploy into `lib/` |
| `index.html` | `/index.html` | Web UI (form + syntax cheat sheet) |
| — | `/settings.toml` | Device-only: `CIRCUITPY_WIFI_SSID` / `CIRCUITPY_WIFI_PASSWORD` etc. |
| — | `/lib/adafruit_hid/`, `/lib/adafruit_httpserver/` | Dependency libraries (.mpy) |

A `boot.py` may be added on the device (runs before USB enumeration; can customize HID report descriptors via `usb_hid.enable()`).

## 2. System Architecture

```
browser ──HTTP──> adafruit_httpserver (code.py)
                    │  POST /macro   → compile+execute (currently synchronous/blocking)
                    │  POST /compile → compile only, returns disassembly
                    │  GET  /mouse   → quick mouse click/move
                    ▼
              lib/macro.py: Macro
                    │  compile(): DSL text → bytecode
                    │  execute(): VM interprets opcodes, drives HID
                    ▼
         adafruit_hid (Keyboard/KeyboardLayoutUS/Mouse) → usb_hid → USB host
```

The server listens on `http://<wifi.radio.ipv4_address>:80`; the startup address is printed to serial. The frontend submits with `encodeURIComponent` + `application/x-www-form-urlencoded`.

## 3. Macro DSL Syntax (LaTeX-style)

- Plain text is typed verbatim (ASCII 32–126 only); `\n`/`\r\n`/`\r` → Enter, `\t` → Tab
- Escapes: `\\` `\{` `\}`; separate a command from following text with an empty `{}` (`\enter{}abc`)
- Special keys: `\enter` `\esc` `\tab` `\space` `\bs` `\del` `\up/\down/\left/\right` `\home` `\end` `\pgup` `\pgdn` `\ins` `\caps` `\f1`–`\f12`
- Combos: `\ctrl+c`, `\ctrl+\shift+t`, `\alt+\f4`; modifier aliases ctrl/control, shift, alt/option/opt, win/gui/cmd
- Control: `\delay{sec}`, `\rep{N}{body}`, `\kdown{key}`, `\kup{key}` / `\kup{}` (release all)
- Mouse: `\click{L|R|M[,count]}`, `\move{dx,dy}`, `\mdown{btn}`, `\mup{btn}`

> The DSL v2 syntax overhaul (line continuation `\`+newline, `\#` comments, `\pace` splitting \delay's dual semantics, brace-escape fix, bytecode version header and uniform block offsets) is in ROADMAP 1.3. Structural capabilities (subroutines/`\wait`/variables) are explicitly NOT in v2; they are deferred to a unified design with the runtime extension mechanism (signal/slot, `\call{ext}` + import).

## 4. Bytecode & VM

Operands are little-endian; `MAX_BYTECODE = 4096`.

| Opcode | Operands | Semantics |
|---|---|---|
| 0x01 CHAR | ascii | Mapped via `layout.keycodes()` (Shift handled automatically) |
| 0x02 KEY | keycode | Single key tap |
| 0x03 COMBO | mod_mask, keycode | mask bit0-3 = Ctrl/Shift/Alt/GUI; keycode=0 means pure modifier |
| 0x04 DELAY | u16 ms | Sleeps once AND sets the interval after each subsequent action (dual semantics) |
| 0x05 MCLICK | btn, u16 count | |
| 0x06 MMOVE | i16 dx, i16 dy | adafruit_hid auto-chunks ±127 |
| 0x10 LOOP | u8 count, u16 body_len | body inlined at compile time; `loop_stack` holds `(ip, remaining, saved_delay)`; body_len currently unused by the runtime |
| 0x11 LOOP_END | — | Pop stack / jump back |
| 0x12/0x13 KEY_DOWN/KEY_UP | keycode(0=release all) | |
| 0x14/0x15 MDOWN/MUP | btn | |
| 0xFF END | — | |

Design strengths: compile/execute separation (`/compile` previews the disassembly); compact bytecode; loops via jump+stack, no runtime recursion; `layout.keycodes()` correctly handles Shift characters like `!@#`.

## 5. Known Issues (scheduled in ROADMAP Phase 1.2)

1. **B1**: `MAX_NEST=2` defined but never enforced — `\rep` nesting is actually unlimited; deep nesting can blow the compile recursion stack; the HTML docs claim 2 levels
2. **B2**: `execute()` does not release keys on exception paths — a throw after `KEY_DOWN` leaves keys stuck; needs try/finally `release_all()`
3. **B3**: `code.py`'s `htmldecode(urldecode(code))` correctness depends on "adafruit_httpserver's `form_data` does NOT URL-decode" (verified on current main source: splits only, no unquote) — **the library version must be pinned**; `htmldecode` also wrongly converts legitimate `&lt;` literals in macros — misplaced defensiveness, should be removed
4. **B4**: HTML docs say "1024 bytes / 2 nesting levels", code actually allows 4096 / unlimited
5. **B5**: dead code `_emit_i8`; OP_CHAR's `layout.write()` fallback is basically unreachable and has inconsistent hold semantics
6. `_read_braces` does not recognize escapes when counting depth: a body containing `\{` / `\}` / `\\` misreports "Unmatched {" (fixed in ROADMAP 1.3)
7. OP_LOOP's `body_len` is a dead parameter (read but unused by the runtime) — DSL v2 will generalize it into the uniform jump offset for all block structures (ROADMAP 1.3)
8. Execution is synchronous/blocking with no abort (solved by ROADMAP 1.4 asyncio); no authentication (any LAN peer can inject keystrokes; 1.4 adds Bearer)
9. `urldecode` treats UTF-8 bytes as latin-1: non-ASCII input becomes mojibake before failing compilation (misleading error messages)

## 6. CircuitPython Cheat Sheet

**Architecture**: Adafruit's fork of MicroPython. Boot flow: `boot.py` (before USB enumeration) → mount CIRCUITPY drive → auto-run `code.py` (auto-reload on save). USB exposes both a serial REPL (CDC) and the HID device.

**Hardware prerequisite**: `usb_hid` needs native USB OTG → only ESP32-S2/S3 work (classic ESP32 has no native USB; C3 has only USB-Serial-JTAG). Use CircuitPython 10.x firmware; 4MB-flash S2/S3 boards need TinyUF2 ≥ 0.33.0.

**Configuration**: `settings.toml` (8.0+) stores WiFi credentials etc.; networking uses the built-in `wifi` + `socketpool` (the stack runs on the other core; Python is single-threaded cooperative).

**Library management**: libraries ship as .mpy in the [Adafruit Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle); on PC use `circup install adafruit_hid adafruit_httpserver` (must match the firmware major version).

**Dev workflow**: edit files on the CIRCUITPY drive directly, save to reboot; serial REPL debugging (`tio`/`picocom`; this project prints disassembly to serial); ESP32-S2/S3 support Web Workflow (`CIRCUITPY_WEB_API_PASSWORD`, HTTP REST `/fs/...` for remote file edits); without hardware, run unit tests on CPython with a mock HID object (`Macro` only depends on the keyboard/layout/mouse trio + Keycode constants + `time`).

**Differences from CPython**: no full stdlib; `str` lacks `isalnum/isalpha` (this project works around it with an `_ALPHA` lookup table); single-precision floats; limited RAM — watch recursion depth; f-strings work.

**Key API facts (verified)**:
- `adafruit_hid.Mouse.move()` internally chunks movements >±127, so the VM's i16 moves need no manual chunking
- adafruit_httpserver's `form_data` / `query_params` **do NOT URL-decode** (current main source only splits, never unquotes) — `code.py`'s manual `urldecode` relies on this behavior, hence the version pin
- httpserver's main branch already has WebSocket / SSE / Basic/Bearer auth available

## 7. Extension Capability Conclusions (research settled; adopt directly)

**Coroutines**: CircuitPython ships `asyncio` built in (stable on S3); `server.poll()` is non-blocking and wrappable in a task; once macro execution is async it can run concurrently with HTTP and be aborted via `task.cancel()`. Known risk: hard fault reports for asyncio + native TCP sockets on the espressif port ([#10775](https://github.com/adafruit/circuitpython/issues/10775)) — raw socket features need early on-device validation.

**Networking**: `wifi`/`socketpool`/`ssl` built in; `adafruit_requests` (HTTP client) and `adafruit_minimqtt` (MQTT) ready-made; httpserver supports WebSocket/SSE; S3 has enough RAM for HTTPS.

**Image recognition**:
- Foundation is in place: `ulab` (built-in numpy-style ndarray with 2D `convolve` and dot), `espcamera` (built-in camera module), `jpegio` (JPEG decode)
- Template/pixel matching is feasible on ulab; the preferred image source is "host screenshots pushed over the network" (an HID device cannot see the screen)
- **Must choose an S3 board with PSRAM** (CircuitPython places the Python heap in PSRAM; large image buffers only fit there)

**Neural networks (CNN)**:
- ESP32-S3 has no dedicated NPU; NN acceleration relies on SIMD vector instructions, usable only via Espressif C libraries (esp-nn/ESP-DL), and dynamic .mpy native modules cannot link against ESP-IDF — **acceleration must be compiled into the firmware**
- No ready ESP-DL/TFLM Python bindings exist; TFLM has mature MicroPython precedent: microlite ([mocleiri/tensorflow-micropython-examples](https://github.com/mocleiri/tensorflow-micropython-examples) + [S3 port](https://github.com/M-D-777/tensorflow-micropython-ESP32-S3-EYE)), ~10× speedup with esp-nn
- Settled route: **the platform decision moves up to Phase 3.1** — CP-side ulab benchmarks run in parallel with an MP-side spike; "verify first, migrate second"; vision code is only written on the decided platform; if staying on CP: ulab micro forward pass → dual-chip ESP-DL fallback; if migrating to MP: microlite (TFLM+esp-nn); host-side inference serves as the baseline throughout

**MicroPython migration facts (spike basis, all verified)**:
- HID: MicroPython 1.23+ has built-in `machine.USBDevice` (S3 supported); the official micropython-lib provides ready pure-Python `usb-device-keyboard`/`usb-device-mouse` classes — no hand-written USB descriptors; only the US layout mapping table needs porting (a hundred lines of static data)
- asyncio: built into firmware and more mature — task cancellation, Event/Lock/ThreadSafeFlag, **Stream + `open_connection`/`start_server` (TCP incl. SSL)** all present; its socket path is mature, so CP's #10775 risk does not carry over
- Extension mechanism: `USER_C_MODULES` is an official mechanism; microlite can be hooked in without forking the core
- Migration cost: adafruit_httpserver → self-built asyncio HTTP or microdot; circup → mip; `settings.toml` auto-WiFi → a few lines of `network.WLAN`

## 8. References

- CircuitPython: https://circuitpython.org / https://docs.circuitpython.org / [Releases](https://github.com/adafruit/circuitpython/releases) / [Espressif port](https://docs.circuitpython.org/en/10.1.2/ports/espressif/README.html)
- [adafruit_hid](https://docs.circuitpython.org/projects/hid/en/latest/) (see source for mouse.move chunking) / [adafruit_httpserver](https://github.com/adafruit/Adafruit_CircuitPython_HTTPServer)
- [ulab](https://circuitpython.readthedocs.io/en/latest/shared-bindings/ulab/) / [espcamera](https://docs.circuitpython.org/en/latest/shared-bindings/espcamera/)
- [CP asyncio on ESP32-S3 tutorial](https://learn.adafruit.com/adafruit-metro-esp32-s3/asyncio)
- [MicroPython asyncio docs](https://docs.micropython.org/en/latest/library/asyncio.html) / [micropython-lib USB driver packages](https://github.com/micropython/micropython-lib/tree/master/micropython/usb) / [machine.USBDevice](https://docs.micropython.org/en/latest/library/machine.USBDevice.html)
- [Espressif tflite-micro-esp-examples](https://github.com/espressif/tflite-micro-esp-examples) / [ESP-DL](https://www.espressif.com/en/news/ESP-DL)
- [MicroPython S3 AI acceleration discussion (source of the "user C module required" conclusion)](https://github.com/orgs/micropython/discussions/14117)
