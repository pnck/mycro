# AGENTS.md — MYCRO Cold-Start Manual

A USB HID keyboard/mouse macro device based on CircuitPython: an ESP32-S3 emulates keyboard and mouse over USB while serving WiFi HTTP; users write LaTeX-style macro scripts, which the device **compiles to custom bytecode** and runs on a built-in **stack VM**.

See [ROADMAP.md](ROADMAP.md) for the evolution plan. **Architectural discipline: the compiler/VM/protocol layers stay runtime-agnostic; all hardware interaction goes through the HIDProvider abstraction.**

## 1. Repo Structure & Deployment

```
src/
├── code.py           → /code.py       Entry point: asyncio main loop (HTTP + macro task + raw stub)
├── index.html        → /index.html    Web UI (macro edit/run + Auto Clicker + cheat sheet)
└── lib/
    ├── macro.py      → /lib/macro.py  Compiler + VM (pure Python, zero adafruit dependencies)
    ├── keymap.py     → /lib/keymap.py HID usage ID constants (page 0x07)
    ├── runtime.py    → /lib/runtime.py Runtime registry (namespaces/signals/slots)
    ├── codec.py      → /lib/codec.py  Wire codec: frame packing/reassembly + auth header (pure Python)
    └── hid_adafruit.py → /lib/hid_adafruit.py  HIDProvider implementation on adafruit_hid
tests/                  CPython unit tests (MockHID + pytest)
tools/deploy.sh         Deploy: CIRCUITPY=/mountpoint ./tools/deploy.sh (exact-set sync via on-device manifest + circup lib install)
docs/protocol.md        Machine protocol v1: wire format + ADR (normative client reference)
requirements-device.txt  Device-side library list (circup version pinning)
```

Device-only: `/settings.toml`, `/lib/adafruit_hid/`, `/lib/adafruit_httpserver/`. A `boot.py` (runs before USB enumeration) can customize HID report descriptors.

**settings.toml keys (deploy.sh merges conservatively: existing vars untouched, missing ones appended empty)**:
- `MYCRO_WIFI_SSID` / `MYCRO_WIFI_PASSWORD` — **deliberately NOT the built-in `CIRCUITPY_WIFI_*`**: the built-in keys make the supervisor connect WiFi before USB MSC initializes, so a network outage hangs the device at boot and takes away even the CIRCUITPY drive. We connect at runtime in code.py; on failure the device degrades to offline mode (USB/serial/HID all work)
- `MYCRO_TOKEN` — Bearer token; empty/unset = dev mode (serial warning)

**HID abstraction layer**: `Macro(hid)`; provider interface = `key_press/key_release/keys_release_all/char_keycodes/mouse_press/mouse_release/mouse_move`. The device uses `hid_adafruit.py`; unit tests use `tests/mock_hid.py`; a future MicroPython migration only needs a new provider + the code.py platform layer.

## 2. System Architecture (asyncio)

`asyncio.run()` main loop: first `_wifi_connect()` (runtime WiFi connect, 3 attempts with timeout; failure → offline-mode idle loop, USB/serial/HID unaffected — fix settings.toml and reset to retry), then `server.start()` and concurrent drivers:
- **`_http_loop`**: `server.poll()` HTTP polling
- **Macro execution task**: `POST /macro` starts in the background via `asyncio.create_task` after compiling; HTTP stays responsive during execution
- **`_raw_stub`**: raw TCP listener on `:7373` (message protocol arrives in Phase 2; fully exception-isolated — CP espressif asyncio TCP has known risk #10775)

| Endpoint | Role |
|---|---|
| GET `/` | index.html |
| POST `/macro` | Compile and start execution **in the background**; returns `Started: NB` immediately |
| GET `/macro/status` | `idle` / `running` / `done` / `error <msg>` / `aborted` |
| POST `/macro/abort` | `task.cancel()`; `execute()`'s finally guarantees all keys/buttons released |
| POST `/compile` | Compile only, returns the disassembly |

**Auth**: once `MYCRO_TOKEN` is set in settings.toml, all endpoints except `/` require `Authorization: Bearer <token>`; unset = dev mode (serial warning).
**Input decoding**: the frontend uses `encodeURIComponent` + `x-www-form-urlencoded`; `form_data` does **NOT** URL-decode (verified against the pinned library source), so code.py runs a manual `urldecode` — library upgrades must re-verify this assumption.

## 3. Macro DSL Syntax (v3 landed)

**Basics (since v2)**: plain text is typed verbatim (ASCII 32–126); `\n`/`\r\n`/`\r` → Enter, `\t` → Tab; escapes `\\` `\{` `\}` `\$`; `\` + newline = line continuation; `\# ...` = comment; special keys `\enter` `\esc` `\tab` `\space` `\bs` `\del`, arrows, `\home` `\end` `\pgup` `\pgdn` `\ins` `\caps` `\f1`–`\f12`; combos `\ctrl+c` etc.; mouse `\click{L|R|M|B|F[,count]}` (B/F = side buttons), `\move{dx,dy}`, `\mdown`, `\mup`; `\delay{sec}` (single), `\pace{sec}` (subsequent interval), `\rep{N}{body}` (nesting ≤4), `\kdown`/`\kup`.

**Structure (v3)**:
- User macros: `\def{name}[argc]{body}` + `\name{arg}...` (compile-time expansion; `#1`–`#9` params, `##` is a literal #)
- Variables: 16 i32 registers, `\set{x}{v}` / `\add{x}{v}` (`0x` hex supported); `$ret`/`$timeout` special read-only; `$sig.field` signal slots (READSLOT desugar)
- Conditionals: `\ifnum{$a}{op}{b}{then}[{else}]` (`= != < <= > >=`)
- Rendering: `\val{name}` (types out decimal digits at text position; integer semantics use value-position `$name`)
- Extension: `\use{ns}` (compile-time dependency check), `\call{ns.fn}{arg}...` (return → `$ret`), `\wait{sig}[{sec}]` (waits for the **next** fire after entry — a fire with no waiter is discarded; timeout → `$timeout`)
- Key rules: `$` interpolation only takes effect in **value positions** (literal in code bodies/plain text); greedy match within value positions, `${name}` to delimit; built-in command names are ≥2 letters

Full semantics and the type system (signatures/handles/erasure): see ROADMAP "DSL v3 — Syntax Extension".

## 4. Bytecode & VM

Format: `MAGIC(0xA5) + VERSION(3) + string table (u8 count; u8 len+bytes)` + opcode sequence; operands little-endian; `MAX_BYTECODE = 4096`. execute/disassemble verify the version header. Registers: 0x00–0x0F user i32 (zeroed at execution start), 0xFD scratch, 0xFE=`$timeout`, 0xFF=`$ret`.

| Opcode | Operands | Semantics |
|---|---|---|
| 0x01 CHAR | ascii | Mapped via provider `char_keycodes()` (Shift handled automatically) |
| 0x02 KEY | keycode | Single key tap |
| 0x03 COMBO | mod_mask, keycode | mask bit0-3 = Ctrl/Shift/Alt/GUI; keycode=0 means pure modifier |
| 0x04 SLEEP | typed(u16) | Single sleep (DSL `\delay`) |
| 0x05 MCLICK | typed(u8) btn, typed(u16) count | |
| 0x06 MMOVE | typed(i16) dx, typed(i16) dy | adafruit_hid auto-chunks ±127 |
| 0x07 PACE | typed(u16) | Set the interval after subsequent actions (DSL `\pace`) |
| 0x10 LOOP | typed(u8) count, u16 len | len = body size in bytes (incl. LOOP_END); bounds check; count=0 skips |
| 0x11 LOOP_END | — | Pop stack / jump back |
| 0x12/0x13 KEY_DOWN/KEY_UP | typed(u8) keycode(0=release all) | |
| 0x14/0x15 MDOWN/MUP | typed(u8) btn | |
| 0x20/0x21 SET/ADD | reg, typed(i32) | i32 wraparound |
| 0x22 BRA | cc, reg, typed(i32), u16 off | On false condition `ip += off` (compiled from `\ifnum`) |
| 0x23 JMP | u16 off | Skip the else branch |
| 0x28 READSLOT | reg, sig, field | Read a signal slot into a register |
| 0x29 WAIT | sig, typed(u32) timeout_ms | Suspend until signal; result written to `$timeout` |
| 0x2A CALL_EXT | ns, fn, argc, typed args | Runtime extension call; return written to `$ret` |
| 0x2B TYPEREG | reg | Render decimal digits (DSL `\val`, one pacing action) |
| 0xFF END | — | |

typed = tag(0=imm/1=reg/2=str) + payload; tag=0 is wire-identical to v2. The string table holds signal/ns/fn/template names; slots are reused via READSLOT into scratch.

Design strengths: compile/execute separation (`/compile` previews the disassembly); loops/ifs are pure ip jumps with zero runtime stack growth; execute's finally guarantees all keys/buttons released on every exit path (incl. cancel); semantic types (color/handles) are compile-time checked and runtime-erased — the bytecode physical tags stay at three forever. The compiler nests via an explicit heap stack (no Python recursion): the device pystack is only a few KB, so nesting depth is bounded by bytecode size, not call frames.

## 5. Current Limitations

- Argument styles not unified (`\kdown{ctrl}` brace style vs `\ctrl+c` chained) — minor; to be settled with later structural capabilities
- `urldecode` treats UTF-8 bytes as latin-1: non-ASCII input becomes mojibake before failing compilation (misleading error messages)

## 6. CircuitPython Cheat Sheet

**Architecture**: Adafruit's fork of MicroPython. Boot flow: `boot.py` (before USB enumeration) → mount CIRCUITPY drive → auto-run `code.py` (auto-reload on save). USB exposes both a serial REPL (CDC) and the HID device.

**Hardware prerequisite**: `usb_hid` needs native USB OTG → only ESP32-S2/S3 work (classic ESP32 has no native USB; C3 has only USB-Serial-JTAG). Use CircuitPython 10.x firmware; 4MB-flash S2/S3 boards need TinyUF2 ≥ 0.33.0.

**Configuration**: `settings.toml` (8.0+) stores environment variables; this project uses custom `MYCRO_WIFI_*` keys with a runtime connect (see the pitfall note in §1). Networking uses the built-in `wifi` + `socketpool` (the stack runs on the other core; Python is single-threaded cooperative).

**Library management**: libraries ship as .mpy in the [Adafruit Bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle); on PC use `circup install adafruit_hid adafruit_httpserver` (must match the firmware major version).

**Dev workflow**: local unit tests `pytest tests/` (this repo's dev container uses `/opt/venv/bin/pytest`); deploy `CIRCUITPY=/mountpoint ./tools/deploy.sh`; on device, edit CIRCUITPY drive files directly and save to reboot; serial REPL debugging (`tio`/`picocom`; compiled macro disassembly prints to serial); ESP32-S2/S3 support Web Workflow (`CIRCUITPY_WEB_API_PASSWORD`, HTTP REST `/fs/...` for remote file edits).

**Differences from CPython**: no full stdlib; `str` lacks `isalnum/isalpha` (this project works around it with an `_ALPHA` lookup table); single-precision floats; limited RAM — watch recursion depth; f-strings work.

**Key API facts (verified)**:
- `adafruit_hid.Mouse.move()` internally chunks movements >±127 (verified `_limit` exists in the on-device mouse.mpy), so the VM's i16 moves need no manual chunking
- adafruit_httpserver's `form_data` / `query_params` **do NOT URL-decode** (verified in the on-device request.mpy: utf-8 decode only, no unquote logic) — `code.py`'s manual `urldecode` relies on this behavior, hence the version pin
- httpserver needs ≥ 4.5.x (has `start()`/`stop()`/`poll()`; the `server.start()` usage verified on the installed version; version snapshots live in private memory)
- httpserver's main branch already has WebSocket / SSE / Basic/Bearer auth available

## 7. Extension Capability Conclusions (research settled; adopt directly)

**Coroutines**: **CircuitPython 10.x firmware ships only the `_asyncio` C core; the user-facing `asyncio` package is provided by the bundle library `adafruit-circuitpython-asyncio`** (the 10.0.0 release notes say "Ensure you are using the latest version of the asyncio CircuitPython library") — `circup install asyncio` is the standard step for ALL boards; the library is officially maintained by Adafruit, CI-tested, based on MicroPython uasyncio, and its Ticks dependency is auto-installed by circup. `server.poll()` is non-blocking and wrappable in a task; once macro execution is async it can run concurrently with HTTP and be aborted via `task.cancel()`. Known risk: hard fault reports for asyncio + native TCP sockets on the espressif port ([#10775](https://github.com/adafruit/circuitpython/issues/10775)) — raw socket features need early on-device validation.

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
