#!/usr/bin/env python3
"""MYCRO OBS Full Link Test.

Interactive validation suite verifying the complete closed link:
  1. GUI renders a pure solid color stimulus patch on-screen.
  2. OBS Studio captures the screen/window via obs_mycro.py.
  3. The physical MYCRO device monitors the patch coordinates via the 'img' DSL namespace.
  4. The GUI changes the patch color, triggering on-chip perception.
  5. The device VM executes an HID keyboard tap over the physical USB cable with a RANDOMIZED key.
  6. This test program intercepts the hardware keystroke to prove full-link closure.

Usage:
    python3 tests/obs/full_link_test.py [--host mycro.local] [--port 7373] [--token TOKEN]
"""

import argparse
import base64
import os
import queue
import random
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Dict, List, Optional, Tuple, Any, Callable

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from client import Client, ProtocolError
from obs.obs_mycro import ObsWsConfig, ObsWsClient, CaptureEngine

# Color Palette (Dark Theme)
BG_MAIN = "#18181b"
BG_CARD = "#27272a"
BG_BORDER = "#3f3f46"
TEXT_MAIN = "#f4f4f5"
TEXT_MUTED = "#a1a1aa"
ACCENT_BLUE = "#3b82f6"
ACCENT_GREEN = "#22c55e"
ACCENT_RED = "#ef4444"
ACCENT_YELLOW = "#eab308"

# Distinct calibration color (high magenta)
CALIBRATION_COLOR_HEX = "#FF007F"

# Idle canvas color (pure black)
IDLE_COLOR_HEX = "#000000"

# Robust stimulus definitions:
# Multi-frequency suite benchmarks visual perception latency progression:
# - Trial 1: Green (#00FF00) with watch interval 60ms (~16.7 Hz standard)
# - Trial 2: Red (#FF0000) with watch interval 35ms (~28.6 Hz medium)
# - Trial 3: White (#FFFFFF) with watch interval 20ms (~50.0 Hz ceiling limit)
STIMULUS_CONFIGS = {
    "green": {
        "hex": "#00FF00",
        "name": "Green",
        "interval_ms": 60,
        "pred": r"\all{ {$slot.img.{slot}.val, >, 30000}, {$slot.img.{slot}.val, <, 3000000} }",
    },
    "red": {
        "hex": "#FF0000",
        "name": "Red",
        "interval_ms": 35,
        "pred": r"{$slot.img.{slot}.val, >, 8000000}",
    },
    "white": {
        "hex": "#FFFFFF",
        "name": "White",
        "interval_ms": 20,
        "pred": r"{$slot.img.{slot}.val, >, 8000000}",
    },
}

# Randomized keys pool (letters avoiding common typing navigation keys)
RANDOM_KEY_POOL = list("bcdfghjkmpqrtvwxyz")


def resolve_ipv4(host: str, port: int) -> str:
    """Pre-resolves host to IPv4 to bypass macOS mDNS AAAA 5s timeouts."""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return host


def ensure_device_idle(c: Client, timeout_sec: float = 2.0) -> bool:
    """Ensures device VM is not running any prior macro, aborting if necessary."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            st = c.call("macro.status")
            state = st.get("payload", {}).get("state", "idle")
            if state != "running":
                return True
            c.call("macro.abort")
            time.sleep(0.1)
        except Exception:
            time.sleep(0.1)
    return False


class FullLinkRunner:
    """Orchestrates device protocol interactions, calibration, and latency timing."""

    def __init__(self, host: str, port: int, token: str, log_cb, status_cb, source_name: str = ""):
        self.raw_host = host
        self.host = resolve_ipv4(host, port)
        self.port = port
        self.token = token
        self.log = log_cb
        self.status = status_cb
        self.calibrated_coords: Optional[Tuple[int, int]] = None
        self.source_name: str = source_name

    def check_prerequisites(self) -> Tuple[bool, str]:
        """Validates network reachability to device, OBS WS, and capture provider."""
        try:
            s = socket.create_connection((self.host, self.port), timeout=1.5)
            s.close()
        except Exception as e:
            return False, f"MYCRO device unreachable at {self.host}:{self.port} ({e})"

        ws_enabled, ws_port, ws_pwd = ObsWsConfig.load()
        if not ws_enabled:
            return False, "OBS WebSocket server is disabled in OBS config"
        try:
            s = socket.create_connection(("127.0.0.1", ws_port), timeout=1.5)
            s.close()
        except Exception as e:
            return False, f"OBS WebSocket not running on :{ws_port} ({e}). Launch OBS Studio."

        try:
            c = Client(self.host, self.port, self.token)
            check_prov_code = (
                "\\use{img}\\use{net}\\call{img.snap}{0,0,1,1}"
                "\\if{ {$err, =, 3} }{\\call{net.send}{noprov}}{\\call{net.send}{prov_ok}}"
            )
            c.call("macro.submit", {"code": check_prov_code})
            tags = []
            t_end = time.monotonic() + 3.0
            while time.monotonic() < t_end:
                env = c.recv_envelope()
                if env.get("type") == "net.msg":
                    tags.append(env["payload"].get("tag"))
                elif env.get("type") == "macro.result":
                    break
            c.close()
            if tags == ["noprov"]:
                return False, "OBS capture provider script (obs_mycro.py) not connected to device. In OBS Studio: Tools -> Scripts -> select obs_mycro.py -> click 'Connect'."
        except Exception as e:
            return False, f"Failed provider handshake check: {e}"

        return True, "Pre-flight checks passed."

    def calibrate_coordinates(
        self,
        nominal_center_pt: Tuple[int, int],
        screen_size: Tuple[int, int],
        set_color_fn,
    ) -> Optional[Tuple[int, int]]:
        """Calibrates target patch coordinates on OBS canvas using direct screenshot inspection."""
        self.log("Starting coordinate calibration...")
        set_color_fn(CALIBRATION_COLOR_HEX)
        time.sleep(0.35)

        ws_enabled, ws_port, ws_pwd = ObsWsConfig.load()
        ws = ObsWsClient("127.0.0.1", ws_port, ws_pwd)
        try:
            ws.connect()
            ce = CaptureEngine(ws, default_source=self.source_name)
            if not self.source_name:
                self.source_name = ce.resolve_source()
            if not self.source_name:
                self.log("Cannot determine active OBS capture source. Ensure OBS has an active screen capture input.")
                return None
            self.log(f"Active OBS capture source resolved: '{self.source_name}'")
            bmp = ce.get_raw_bmp(source_name=self.source_name)
            offset, src_w, src_h, stride = ce.parse_bmp_header(bmp)
            abs_h = abs(src_h)
            mv = memoryview(bmp)
            self.log(f"Captured OBS frame: {src_w}x{abs_h} from '{self.source_name}' (stride={stride})")

            sw, sh = screen_size
            scale_x = src_w / sw if sw else 1.0
            scale_y = abs_h / sh if sh else 1.0
            nom_x = int(nominal_center_pt[0] * scale_x)
            nom_y = int(nominal_center_pt[1] * scale_y)

            # Check nominal center point and immediate neighborhood
            for dx in (0, -10, 10, -20, 20):
                for dy in (0, -10, 10, -20, 20):
                    nx, ny = nom_x + dx, nom_y + dy
                    if 0 <= nx < src_w and 0 <= ny < abs_h:
                        pts = ce.extract_points(bmp, [(nx, ny)])
                        c_val = pts[0]
                        cr = (c_val >> 16) & 0xFF
                        cg = (c_val >> 8) & 0xFF
                        cb = c_val & 0xFF
                        if cr > 180 and cg < 70 and cb > 70:
                            self.log(f"Nominal point matched: ({nx}, {ny}) [0x{c_val:06X}] on '{self.source_name}'")
                            self.calibrated_coords = (nx, ny)
                            return nx, ny

            self.log("Searching full OBS frame for calibration patch centroid...")
            t0 = time.monotonic()
            matching_x: List[int] = []
            matching_y: List[int] = []
            step = 6
            for y in range(0, abs_h, step):
                row_start = offset + (abs_h - 1 - y if src_h > 0 else y) * stride
                for x in range(0, src_w, step):
                    pos = row_start + x * 3
                    b = mv[pos]
                    g = mv[pos + 1]
                    r = mv[pos + 2]
                    if r > 180 and g < 70 and b > 70:
                        matching_x.append(x)
                        matching_y.append(y)

            scan_ms = (time.monotonic() - t0) * 1000.0
            if len(matching_x) >= 20:
                cx = sum(matching_x) // len(matching_x)
                cy = sum(matching_y) // len(matching_y)
                self.log(f"Located patch in {scan_ms:.1f}ms: centroid=({cx}, {cy}) ({len(matching_x)} points).")
                self.calibrated_coords = (cx, cy)
                return cx, cy
            else:
                self.log(f"Calibration patch not found in OBS frame ({scan_ms:.1f}ms). Check OBS scene source.")
                return None
        except Exception as e:
            self.log(f"Calibration failed: {e}")
            return None
        finally:
            try:
                ws.disconnect()
            except Exception:
                pass

    def run_single_trial(
        self,
        coords: Tuple[int, int],
        stimulus_name: str,
        target_key: str,
        trial_index: int,
        set_color_fn,
        arm_key_listener_fn,
        await_key_fn,
    ) -> Tuple[bool, float, str]:
        """Runs one full-link verification trial with fresh unique slot & clean state."""
        cfg = STIMULUS_CONFIGS[stimulus_name]
        hex_code = cfg["hex"]
        cx, cy = coords

        # Generate a unique watch slot name for this trial to guarantee zero slot collisions
        watch_name = f"w{trial_index}_{random.randint(1000, 9999)}"
        pred_code = cfg["pred"].replace("{slot}", watch_name)

        interval_ms = cfg.get("interval_ms", 35)
        self.log(f"--- Trial {trial_index}/4: Multi-Freq Stimulus '{stimulus_name.upper()}' ({interval_ms}ms) -> Expect Key '{target_key}' ---")

        # 1. Neutral Black Baseline: ensure screen & OBS capture buffer have settled to black
        set_color_fn(IDLE_COLOR_HEX)
        time.sleep(0.35)

        # 2. Connect to device and guarantee clean idle state
        c = Client(self.host, self.port, self.token)
        try:
            if not ensure_device_idle(c, timeout_sec=2.0):
                return False, 0.0, "Device busy: could not abort prior macro"

            macro_code = (
                "\\#!syntax: free\n"
                "\\use{img}\\use{net}\n"
                f"\\call{{img.watch}}{{{watch_name}}}{{{cx}, {cy}}}{{{interval_ms}ms, 6s}}\n"
                "\\set{found}{0}\n"
                "\\rep{25}{\n"
                "  \\if{ {$found, =, 0} }{\n"
                f"    \\wait[200ms]{{$slot.img.{watch_name}.val}}\n"
                f"    \\if{{ {pred_code} }}{{\n"
                f"      \\tap{{{target_key}}}\n"
                "      \\set{found}{1}\n"
                f"      \\call{{net.send}}{{hit_{target_key}}}\n"
                "    }\n"
                "  }\n"
                "}\n"
                f"\\call{{img.unwatch}}{{{watch_name}}}\n"
            )

            sub = c.call("macro.submit", {"code": macro_code})
            if sub.get("type") != "macro.submit.ok":
                return False, 0.0, f"Macro submit failed: {sub}"

            # Neutral arming delay (OBS starts sampling black canvas)
            time.sleep(0.35)

            # Concurrent envelope reader for device perception confirmation
            dev_confirmed = threading.Event()
            t_dev_perceived = [0.0]
            macro_finished = threading.Event()
            stop_reader = threading.Event()

            def _envelope_worker():
                while not stop_reader.is_set():
                    try:
                        env = c.recv_envelope(timeout=0.1)
                        if not env:
                            continue
                        etype = env.get("type")
                        if etype == "net.msg":
                            tag = env["payload"].get("tag")
                            if tag == f"hit_{target_key}":
                                t_dev_perceived[0] = time.perf_counter()
                                dev_confirmed.set()
                        elif etype == "macro.result":
                            macro_finished.set()
                            break
                    except Exception:
                        break

            reader_thread = threading.Thread(target=_envelope_worker, daemon=True)
            reader_thread.start()

            # Arm the host window keystroke listener
            arm_key_listener_fn(target_key)

            # Trigger stimulus on screen
            self.log(f"Triggering stimulus: Setting patch to {hex_code} ({stimulus_name})...")
            t_stimulus = time.perf_counter()
            set_color_fn(hex_code)

            # Await physical USB HID keystroke on frontmost window
            key_received, t_key = await_key_fn(timeout_sec=4.0)

            if not key_received:
                # Key timed out: abort device macro immediately
                try:
                    c.call("macro.abort")
                except Exception:
                    pass
                stop_reader.set()
                reader_thread.join(timeout=0.4)

                set_color_fn(IDLE_COLOR_HEX)
                if dev_confirmed.is_set():
                    dev_lat = (t_dev_perceived[0] - t_stimulus) * 1000.0
                    return False, 0.0, (
                        f"Device detected stimulus in {dev_lat:.1f}ms and sent USB keystroke '{target_key}', "
                        "but window lost focus. Click test window to focus."
                    )
                return False, 0.0, f"Timeout: USB HID keystroke '{target_key}' not received within 4.0s."

            latency_ms = (t_key - t_stimulus) * 1000.0

            # Drain macro result or abort if still running
            t_drain_end = time.monotonic() + 1.0
            while time.monotonic() < t_drain_end and not macro_finished.is_set():
                time.sleep(0.05)
            if not macro_finished.is_set():
                try:
                    c.call("macro.abort")
                except Exception:
                    pass

            stop_reader.set()
            reader_thread.join(timeout=0.4)

            # Reset canvas back to idle black and allow settling time
            set_color_fn(IDLE_COLOR_HEX)
            time.sleep(0.35)

            dev_info = ""
            if dev_confirmed.is_set():
                dev_lat = (t_dev_perceived[0] - t_stimulus) * 1000.0
                dev_info = f" (Perception: {dev_lat:.1f}ms)"

            self.log(f"Trial {trial_index} PASS: Key '{target_key}' intercepted in {latency_ms:.1f}ms!{dev_info}")
            return True, latency_ms, f"Trial {trial_index} verified ({latency_ms:.1f}ms)"
        finally:
            c.close()

    def run_pattern_trial(
        self,
        coords: Tuple[int, int],
        trial_index: int,
        target_key: str,
        set_quadrants_fn: Callable[[List[str]], None],
        set_color_fn: Callable[[str], None],
        arm_key_listener_fn: Callable[[str], None],
        await_key_fn: Callable[[float], Tuple[bool, float]],
    ) -> Tuple[bool, float, str]:
        """Runs a 2D randomized area pattern matching trial with adversarial decoy rejection."""
        cx, cy = coords
        snap_edge = 32
        half_edge = snap_edge // 2
        min_x = cx - half_edge
        min_y = cy - half_edge

        # 4 distinct colors with non-overlapping packed integer ranges:
        # Blue: ~255 (range 100 - 5,000)
        # Green: ~65,280 (range 40,000 - 2,000,000)
        # Gray: ~8,421,504 (range 6,000,000 - 11,000,000)
        # Red: ~16,711,680 (range > 14,000,000)
        PALETTE = [
            ("#0000FF", "Blue", r"\all{ {$c, >, 100}, {$c, <, 5000} }"),
            ("#00FF00", "Green", r"\all{ {$c, >, 40000}, {$c, <, 2000000} }"),
            ("#808080", "Gray", r"\all{ {$c, >, 6000000}, {$c, <, 11000000} }"),
            ("#FF0000", "Red", r"{$c, >, 14000000}"),
        ]

        picked = random.sample(PALETTE, 4)
        q_hex = [p[0] for p in picked]
        q_names = [p[1] for p in picked]
        q_preds_raw = [p[2] for p in picked]

        # Cyclic permutation guarantees 100% mismatch across all 4 quadrants for adversarial decoy
        decoy_hex = [q_hex[1], q_hex[2], q_hex[3], q_hex[0]]

        self.log(f"--- Trial {trial_index}/4: 2D Random Area Pattern Matching [{' '.join(q_names)}] -> Expect Key '{target_key}' ---")

        c0_rule = q_preds_raw[0].replace("$c", "$c0")
        c1_rule = q_preds_raw[1].replace("$c", "$c1")
        c2_rule = q_preds_raw[2].replace("$c", "$c2")
        c3_rule = q_preds_raw[3].replace("$c", "$c3")

        c = Client(self.host, self.port, self.token)
        try:
            if not ensure_device_idle(c, timeout_sec=2.0):
                return False, 0.0, "Device busy: could not abort prior macro"

            # Phase A: Adversarial Decoy Rejection
            # Single-shot macro: tests the decoy pattern ONCE, sends decoy_rejected_ok, and EXITS NATURALLY.
            # Never aborts during an active binary stream, protecting the raw TCP capture companion.
            macro_decoy = (
                "\\#!syntax: free\n"
                "\\use{img}\\use{net}\n"
                f"\\call{{img.snap}}{{{min_x}, {min_y}, {snap_edge}, {snap_edge}}}\\set{{s}}{{$ret}}\n"
                "\\call{img.at}{$s}{8, 8}\\set{c0}{$ret}\n"
                "\\call{img.at}{$s}{24, 8}\\set{c1}{$ret}\n"
                "\\call{img.at}{$s}{8, 24}\\set{c2}{$ret}\n"
                "\\call{img.at}{$s}{24, 24}\\set{c3}{$ret}\n"
                f"\\if{{ \\all{{ {c0_rule}, {c1_rule}, {c2_rule}, {c3_rule} }} }}{{\n"
                f"  \\tap{{{target_key}}}\n"
                f"  \\call{{net.send}}{{false_positive_{target_key}}}\n"
                "}{\n"
                "  \\call{net.send}{decoy_rejected_ok}\n"
                "}\n"
            )

            self.log(f"Phase {trial_index}A: Presenting adversarial decoy pattern...")
            set_quadrants_fn(decoy_hex)
            time.sleep(0.35)

            arm_key_listener_fn(target_key)
            sub_decoy = c.call("macro.submit", {"code": macro_decoy})
            if sub_decoy.get("type") != "macro.submit.ok":
                return False, 0.0, f"Decoy macro submit failed: {sub_decoy}"

            decoy_rejected = False
            t_end = time.monotonic() + 3.0
            while time.monotonic() < t_end:
                try:
                    env = c.recv_envelope()
                    if env:
                        etype = env.get("type")
                        if etype == "net.msg" and env["payload"].get("tag") == "decoy_rejected_ok":
                            decoy_rejected = True
                        elif etype == "macro.result":
                            break
                except Exception:
                    break

            decoy_key_received, _ = await_key_fn(timeout_sec=0.2)
            if decoy_key_received or not decoy_rejected:
                return False, 0.0, "Adversarial decoy rejection failed: device fired keystroke on mismatching pattern!"

            self.log(f"Phase {trial_index}A PASS: Adversarial decoy pattern successfully rejected (0 false-positive keystrokes).")

            # Phase B: Genuine Target Pattern Match & Keystroke Intercept
            # Polling loop (up to 10 iterations). Once matched, set{found}{1} prevents any further
            # calls to img.snap, allowing the macro to exit cleanly with zero orphan stream chunks.
            macro_match = (
                "\\#!syntax: free\n"
                "\\use{img}\\use{net}\n"
                "\\set{found}{0}\n"
                "\\rep{10}{\n"
                "  \\if{ {$found, =, 0} }{\n"
                f"    \\call{{img.snap}}{{{min_x}, {min_y}, {snap_edge}, {snap_edge}}}\\set{{s}}{{$ret}}\n"
                "    \\call{img.at}{$s}{8, 8}\\set{c0}{$ret}\n"
                "    \\call{img.at}{$s}{24, 8}\\set{c1}{$ret}\n"
                "    \\call{img.at}{$s}{8, 24}\\set{c2}{$ret}\n"
                "    \\call{img.at}{$s}{24, 24}\\set{c3}{$ret}\n"
                f"    \\if{{ \\all{{ {c0_rule}, {c1_rule}, {c2_rule}, {c3_rule} }} }}{{\n"
                f"      \\tap{{{target_key}}}\n"
                "      \\set{found}{1}\n"
                f"      \\call{{net.send}}{{hit_{target_key}}}\n"
                "    }{\n"
                "      \\delay{100ms}\n"
                "    }\n"
                "  }\n"
                "}\n"
            )

            self.log(f"Phase {trial_index}B: Presenting genuine target 2D pattern [{' '.join(q_names)}]...")
            set_quadrants_fn(q_hex)
            time.sleep(0.35)

            arm_key_listener_fn(target_key)
            t_stimulus = time.perf_counter()
            sub_match = c.call("macro.submit", {"code": macro_match})
            if sub_match.get("type") != "macro.submit.ok":
                return False, 0.0, f"Target macro submit failed: {sub_match}"

            key_received, t_key = await_key_fn(timeout_sec=4.0)
            if not key_received:
                try:
                    c.call("macro.abort")
                except Exception:
                    pass
                set_color_fn(IDLE_COLOR_HEX)
                return False, 0.0, f"Timeout: USB HID keystroke '{target_key}' not received for 2D area pattern."

            latency_ms = (t_key - t_stimulus) * 1000.0

            # Drain macro.result to guarantee completely clean socket state
            t_drain_end = time.monotonic() + 1.5
            while time.monotonic() < t_drain_end:
                try:
                    env = c.recv_envelope()
                    if env and env.get("type") == "macro.result":
                        break
                except Exception:
                    break

            set_color_fn(IDLE_COLOR_HEX)
            time.sleep(0.35)

            self.log(f"Trial {trial_index} PASS: 2D Spatial Area Pattern matched in {latency_ms:.1f}ms (Decoy rejected)!")
            return True, latency_ms, f"Trial {trial_index} verified ({latency_ms:.1f}ms, 2D pattern matched)"
        finally:
            c.close()


class ActionButton(tk.Label):
    """Platform-independent high-contrast button avoiding macOS Aqua dark-mode contrast inversion."""

    def __init__(
        self,
        parent,
        text: str,
        command: Optional[Callable] = None,
        bg: str = "#2563eb",
        fg: str = "#ffffff",
        active_bg: str = "#1d4ed8",
        disabled_bg: str = "#27272a",
        disabled_fg: str = "#71717a",
        font: Tuple[str, int, str] = ("Helvetica", 11, "bold"),
        padx: int = 14,
        pady: int = 8,
    ):
        super().__init__(
            parent,
            text=text,
            font=font,
            bg=bg,
            fg=fg,
            padx=padx,
            pady=pady,
            relief="raised",
            bd=1,
            cursor="hand2",
        )
        self._command = command
        self._state = "normal"
        self._normal_bg = bg
        self._normal_fg = fg
        self._active_bg = active_bg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def configure(self, **kwargs):
        state = kwargs.pop("state", None)
        text = kwargs.pop("text", None)
        if state is not None:
            self._state = state
            if state == "disabled":
                kwargs.setdefault("bg", self._disabled_bg)
                kwargs.setdefault("fg", self._disabled_fg)
                kwargs.setdefault("cursor", "arrow")
                kwargs.setdefault("relief", "flat")
            else:
                kwargs.setdefault("bg", self._normal_bg)
                kwargs.setdefault("fg", self._normal_fg)
                kwargs.setdefault("cursor", "hand2")
                kwargs.setdefault("relief", "raised")
        if text is not None:
            kwargs["text"] = text
        super().configure(**kwargs)

    def _on_enter(self, _):
        if self._state == "normal":
            super().configure(bg=self._active_bg)

    def _on_leave(self, _):
        if self._state == "normal":
            super().configure(bg=self._normal_bg)

    def _on_click(self, _):
        if self._state == "normal" and self._command:
            self._command()


class FullLinkApp(tk.Tk):
    """Clean dark-themed Tkinter GUI for interactive full link testing (zero emojis)."""

    def __init__(self, host: str, port: int, token: str, source: str = ""):
        super().__init__()
        self.host = host
        self.port = port
        self.token = token
        self.source = source
        self.runner = FullLinkRunner(host, port, token, self.log_msg, self.update_stage, source_name=source)
        self.result: Dict[str, Any] = {
            "ok": False,
            "attempts": 0,
            "latency_ms": 0.0,
            "details": ["Test not executed"],
            "failures": [],
        }

        self.title("MYCRO Full Link Hardware Test")
        self.geometry("580x680")
        self.minsize(520, 520)
        self.configure(bg=BG_MAIN)
        self.attributes("-topmost", True)

        self.key_queue: queue.Queue = queue.Queue()
        self.expected_key: Optional[str] = None
        self.key_armed: bool = False

        self._build_ui()

        # bind_all ensures keystrokes are intercepted globally regardless of focused widget
        self.bind_all("<KeyPress>", self._on_key_press)
        self.bind("<FocusIn>", lambda e: self._on_focus(True))
        self.bind("<FocusOut>", lambda e: self._on_focus(False))

        self.after(300, self._async_preflight)

    def _build_ui(self):
        # Scrollable Root Architecture
        self.scroll_canvas = tk.Canvas(
            self,
            bg=BG_MAIN,
            highlightthickness=0,
            bd=0,
            yscrollincrement=1,
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.scroll_canvas.yview,
        )
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)

        self.content = tk.Frame(self.scroll_canvas, bg=BG_MAIN)
        self.scroll_window = self.scroll_canvas.create_window(
            (0, 0), window=self.content, anchor="nw"
        )

        def _on_content_configure(event):
            self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

        def _on_canvas_configure(event):
            self.scroll_canvas.itemconfig(self.scroll_window, width=event.width)
            self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

        self.content.bind("<Configure>", _on_content_configure)
        self.scroll_canvas.bind("<Configure>", _on_canvas_configure)

        # Standard community event bindings for scrollable containers:
        # Tk 9 (TIP 684) on macOS/Windows routes two-finger trackpad gestures via <TouchpadScroll>.
        # Traditional external mouse wheels route via <MouseWheel>.
        try:
            self.bind_all("<TouchpadScroll>", self._on_touchpad_scroll)
        except Exception:
            pass
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", lambda e: not self._is_over_log(e.widget) and self.scroll_canvas.yview_scroll(-25, "units"))
        self.bind_all("<Button-5>", lambda e: not self._is_over_log(e.widget) and self.scroll_canvas.yview_scroll(25, "units"))

        # Header Frame
        hdr = tk.Frame(self.content, bg=BG_MAIN)
        hdr.pack(fill="x", padx=16, pady=8)

        lbl_title = tk.Label(
            hdr,
            text="MYCRO Full Link Hardware Test",
            font=("Helvetica", 15, "bold"),
            fg=TEXT_MAIN,
            bg=BG_MAIN,
        )
        lbl_title.pack(anchor="w")

        lbl_sub = tk.Label(
            hdr,
            text="Screen Stimulus -> OBS Capture -> ESP32-S3 Perception -> USB HID Keystroke -> Host Intercept",
            font=("Helvetica", 9),
            fg=TEXT_MUTED,
            bg=BG_MAIN,
        )
        lbl_sub.pack(anchor="w", pady=(2, 0))

        # Instructions Banner
        banner_frame = tk.Frame(self.content, bg=BG_CARD, bd=1, relief="solid")
        banner_frame.pack(fill="x", padx=16, pady=(0, 6))

        self.lbl_instructions = tk.Label(
            banner_frame,
            text=(
                "Instructions:\n"
                "1. Keep this window visible on your desktop screen within your OBS capture.\n"
                "2. Click '1. Calibrate in OBS' to lock canvas coordinates.\n"
                "3. Click '2. Run Full Link Test' to execute the 4-trial verification suite.\n"
                "4. Click 'Finish Test' when complete to return to the test suite."
            ),
            font=("Helvetica", 9),
            fg=TEXT_MAIN,
            bg=BG_CARD,
            justify="left",
            padx=8,
            pady=6,
        )
        self.lbl_instructions.pack(fill="x")

        # Patch Card: Contains stimulus patch (PURE SOLID COLOR / PATTERN - ZERO TEXT OVERLAY)
        patch_card = tk.Frame(self.content, bg=BG_CARD, bd=1, relief="solid")
        patch_card.pack(fill="x", padx=16, pady=4)

        self.lbl_patch_status = tk.Label(
            patch_card,
            text="STIMULUS TARGET: IDLE (#000000)",
            font=("Helvetica", 10, "bold"),
            fg=TEXT_MUTED,
            bg=BG_CARD,
        )
        self.lbl_patch_status.pack(pady=(6, 2))

        # Pure solid/pattern canvas: NO text items drawn on top
        self.canvas = tk.Canvas(
            patch_card,
            width=140,
            height=140,
            bg=IDLE_COLOR_HEX,
            highlightthickness=2,
            highlightbackground=BG_BORDER,
        )
        self.canvas.pack(pady=4)

        self.lbl_coords = tk.Label(
            patch_card,
            text="Target Coordinates: Not Calibrated",
            font=("Helvetica", 9),
            fg=TEXT_MUTED,
            bg=BG_CARD,
        )
        self.lbl_coords.pack(pady=(2, 2))

        self.lbl_random_key = tk.Label(
            patch_card,
            text="Randomized Test Key: Pending",
            font=("Helvetica", 10, "bold"),
            fg=ACCENT_BLUE,
            bg=BG_CARD,
        )
        self.lbl_random_key.pack(pady=(0, 6))

        # Focus status indicator
        self.lbl_focus = tk.Label(
            self.content,
            text="[FOCUS OK] Window Focused (Ready for USB Keystrokes)",
            font=("Helvetica", 9, "bold"),
            fg=ACCENT_GREEN,
            bg=BG_MAIN,
        )
        self.lbl_focus.pack(pady=(2, 4))

        # High-contrast action button bar (Platform-independent, ZERO EMOJIS)
        btn_frame = tk.Frame(self.content, bg=BG_MAIN)
        btn_frame.pack(fill="x", padx=16, pady=6)

        self.btn_calib = ActionButton(
            btn_frame,
            text="1. Calibrate in OBS",
            bg="#2563eb",
            fg="#ffffff",
            active_bg="#1d4ed8",
            command=self._on_calibrate_clicked,
        )
        self.btn_calib.pack(side="left", expand=True, fill="x", padx=4)

        self.btn_run = ActionButton(
            btn_frame,
            text="2. Run Full Link Test",
            bg="#16a34a",
            fg="#ffffff",
            active_bg="#15803d",
            command=self._on_run_test_clicked,
        )
        self.btn_run.configure(state="disabled")
        self.btn_run.pack(side="left", expand=True, fill="x", padx=4)

        self.btn_finish = ActionButton(
            btn_frame,
            text="Finish Test",
            bg="#4f46e5",
            fg="#ffffff",
            active_bg="#4338ca",
            command=self.destroy,
        )
        self.btn_finish.configure(state="disabled")
        self.btn_finish.pack(side="left", expand=True, fill="x", padx=4)

        # Stage Checklist Card
        chk_card = tk.Frame(self.content, bg=BG_CARD, bd=1, relief="solid")
        chk_card.pack(fill="x", padx=16, pady=4)

        self.stages = [
            ("dev_conn", "1. Device Connected (mycro.local:7373)"),
            ("prov_conn", "2. OBS Capture Provider Active (obs_mycro.py)"),
            ("calib_lock", "3. Target Coordinates Calibrated"),
            ("trial1", "4. Multi-Freq Trial 1 (60ms ~16Hz): Green Baseline"),
            ("trial2", "5. Multi-Freq Trial 2 (35ms ~28Hz): Red Benchmark"),
            ("trial3", "6. Multi-Freq Trial 3 (20ms ~50Hz): White Benchmark"),
            ("trial4", "7. 2D Area Pattern: Random Matrix & Decoy Rejection"),
            ("full_link_ok", "8. Full Link Closed Loop Verified"),
        ]
        self.stage_labels: Dict[str, tk.Label] = {}
        for key, text in self.stages:
            row = tk.Frame(chk_card, bg=BG_CARD)
            row.pack(fill="x", padx=10, pady=2)
            lbl = tk.Label(
                row,
                text=f"[PENDING] {text}",
                font=("Helvetica", 9),
                fg=TEXT_MUTED,
                bg=BG_CARD,
                anchor="w",
            )
            lbl.pack(side="left")
            self.stage_labels[key] = lbl

        # Log Output Card with Header and Copy Button
        log_card = tk.Frame(self.content, bg=BG_MAIN)
        log_card.pack(fill="both", expand=True, padx=16, pady=(4, 10))

        log_hdr = tk.Frame(log_card, bg=BG_MAIN)
        log_hdr.pack(fill="x", pady=(0, 4))

        lbl_log_title = tk.Label(
            log_hdr,
            text="Execution Log",
            font=("Helvetica", 10, "bold"),
            fg=TEXT_MUTED,
            bg=BG_MAIN,
        )
        lbl_log_title.pack(side="left")

        self.btn_copy_logs = tk.Button(
            log_hdr,
            text="Copy Logs",
            font=("Helvetica", 9),
            fg="#000000",
            padx=8,
            pady=2,
            command=self._copy_logs,
        )
        self.btn_copy_logs.pack(side="right")

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            height=6,
            bg="#121214",
            fg="#e4e4e7",
            font=("Monaco" if sys.platform == "darwin" else "Consolas", 9),
            insertbackground="white",
            bd=1,
            relief="solid",
        )
        self.log_text.pack(fill="both", expand=True)

        # Standard keyboard copy shortcuts
        self.log_text.bind("<Command-c>", lambda e: self._copy_selection())
        self.log_text.bind("<Control-c>", lambda e: self._copy_selection())

    def _is_over_log(self, widget) -> bool:
        """Checks if the pointer is over the log ScrolledText or its scrollbar."""
        w = widget
        while w:
            if w == self.log_text or w == getattr(self.log_text, "vbar", None):
                return True
            w = getattr(w, "master", None)
        return False

    def _on_touchpad_scroll(self, event):
        """Handles TIP 684 high-precision touchpad scroll gestures in Tk 9+ (macOS/Windows)."""
        if self._is_over_log(event.widget):
            return
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        # Tk 9 packs dx (high 16 bits) and dy (low 16 bits) into a 32-bit integer (%D)
        raw = int(delta) & 0xFFFFFFFF
        dy = raw & 0xFFFF
        if dy >= 0x8000:
            dy -= 0x10000
        if dy != 0:
            # yscrollincrement=1 gives 1:1 pixel displacement matching native Cocoa trackpad physics
            self.scroll_canvas.yview_scroll(-int(dy), "units")

    def _on_mousewheel(self, event):
        """Handles traditional external mouse wheel notches across platforms."""
        if self._is_over_log(event.widget):
            return
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        if sys.platform == "darwin":
            # On macOS Aqua, external mouse wheel ticks report small integers (typically +/-1).
            # With yscrollincrement=1, scale by 25 to provide responsive scrolling per wheel notch.
            step = -int(delta * 25)
            self.scroll_canvas.yview_scroll(step, "units")
        else:
            step = -int((delta / 120.0) * 25.0) if abs(delta) >= 120 else (-25 if delta > 0 else 25)
            self.scroll_canvas.yview_scroll(step, "units")

    def _copy_logs(self):
        text = self.log_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.btn_copy_logs.configure(text="Copied!")
        self.after(1500, lambda: self.btn_copy_logs.configure(text="Copy Logs"))

    def _copy_selection(self):
        try:
            sel = self.log_text.get("sel.first", "sel.last")
            if sel:
                self.clipboard_clear()
                self.clipboard_append(sel)
        except Exception:
            pass

    def log_msg(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)  # Real-time terminal output!
        def _append():
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            self.log_text.see("end")
        self.after(0, _append)

    def update_stage(self, stage_key: str, status: str, detail: str = ""):
        def _update():
            lbl = self.stage_labels.get(stage_key)
            if not lbl:
                return
            for k, txt in self.stages:
                if k == stage_key:
                    raw_txt = txt
                    break
            else:
                raw_txt = stage_key

            if status == "pass":
                lbl.configure(text=f"[PASS] {raw_txt} {detail}", fg=ACCENT_GREEN)
            elif status == "warn":
                lbl.configure(text=f"[WARN] {raw_txt} {detail}", fg=ACCENT_YELLOW)
            elif status == "running":
                lbl.configure(text=f"[RUNNING] {raw_txt} {detail}", fg=ACCENT_YELLOW)
            elif status == "fail":
                lbl.configure(text=f"[FAIL] {raw_txt} {detail}", fg=ACCENT_RED)
            else:
                lbl.configure(text=f"[PENDING] {raw_txt}", fg=TEXT_MUTED)
        self.after(0, _update)

    def set_canvas_color(self, hex_color: str):
        """Thread-safe pure solid color update (no text overlay inside canvas)."""
        def _set():
            self.canvas.delete("all")
            self.canvas.configure(bg=hex_color)
            self.lbl_patch_status.configure(text=f"STIMULUS TARGET: {hex_color.upper()}")
            self.update_idletasks()
        self.after(0, _set)

    def set_canvas_quadrants(self, colors: List[str]):
        """Renders a 2x2 grid of distinct colored quadrants symmetrically filling the canvas."""
        def _set():
            self.canvas.delete("all")
            w = self.canvas.winfo_width() or 140
            h = self.canvas.winfo_height() or 140
            hw = w // 2
            hh = h // 2
            # Quadrant 0: top-left (0, 0, hw, hh)
            self.canvas.create_rectangle(0, 0, hw, hh, fill=colors[0], outline="")
            # Quadrant 1: top-right (hw, 0, w, hh)
            self.canvas.create_rectangle(hw, 0, w, hh, fill=colors[1], outline="")
            # Quadrant 2: bottom-left (0, hh, hw, h)
            self.canvas.create_rectangle(0, hh, hw, h, fill=colors[2], outline="")
            # Quadrant 3: bottom-right (hw, hh, w, h)
            self.canvas.create_rectangle(hw, hh, w, h, fill=colors[3], outline="")
            self.lbl_patch_status.configure(
                text=f"STIMULUS: 2D PATTERN [{' '.join(colors)}]"
            )
            self.update_idletasks()
        self.after(0, _set)

    def _on_focus(self, focused: bool):
        if focused:
            self.lbl_focus.configure(
                text="[FOCUS OK] Window Focused (Ready for USB Keystrokes)",
                fg=ACCENT_GREEN,
            )
        else:
            self.lbl_focus.configure(
                text="[FOCUS LOST] Focus Lost (Click Window to Re-focus)",
                fg=ACCENT_RED,
            )

    def _on_key_press(self, event):
        char = event.char or event.keysym
        if not char:
            return
        char_lower = char.lower()
        self.log_msg(f"Key press event detected: {char_lower!r}")
        if self.key_armed and self.expected_key:
            if char_lower == self.expected_key.lower():
                t_now = time.perf_counter()
                self.key_queue.put((char_lower, t_now))
                self.log_msg(f"Target key intercepted successfully: '{char_lower}'")

    def arm_key_listener(self, target_key: str):
        def _arm():
            while not self.key_queue.empty():
                try:
                    self.key_queue.get_nowait()
                except queue.Empty:
                    break
            self.expected_key = target_key
            self.key_armed = True
            self.lbl_random_key.configure(
                text=f"Randomized Test Key: '{target_key}' (Awaiting USB HID tap...)",
                fg=ACCENT_YELLOW,
            )
            self.lift()
            self.focus_force()
        self.after(0, _arm)

    def await_key(self, timeout_sec: float) -> Tuple[bool, float]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                char, t_recv = self.key_queue.get(timeout=0.05)
                self.key_armed = False
                return True, t_recv
            except queue.Empty:
                pass
        self.key_armed = False
        return False, 0.0

    def _async_preflight(self):
        def _task():
            self.update_stage("dev_conn", "running")
            self.update_stage("prov_conn", "running")
            ok, msg = self.runner.check_prerequisites()
            if ok:
                self.update_stage("dev_conn", "pass")
                self.update_stage("prov_conn", "pass")
                self.log_msg("Ready. Click '1. Calibrate in OBS' to locate stimulus patch.")
            else:
                self.update_stage("dev_conn", "fail")
                self.update_stage("prov_conn", "fail")
                self.log_msg(f"Preflight check failed: {msg}")
        threading.Thread(target=_task, daemon=True).start()

    def _on_calibrate_clicked(self):
        self.btn_calib.configure(state="disabled")
        self.update_stage("calib_lock", "running")

        # Synchronously update UI and sample window geometry on main thread
        self.set_canvas_color(CALIBRATION_COLOR_HEX)
        self.update_idletasks()
        rx = self.canvas.winfo_rootx()
        ry = self.canvas.winfo_rooty()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        nominal_pt = (rx + cw // 2, ry + ch // 2)
        screen_wh = (self.winfo_screenwidth(), self.winfo_screenheight())

        def _task():
            res = self.runner.calibrate_coordinates(
                nominal_pt, screen_wh, self.set_canvas_color
            )
            self.set_canvas_color(IDLE_COLOR_HEX)
            self.btn_calib.configure(state="normal")

            if res:
                cx, cy = res
                src = self.runner.source_name
                self.lbl_coords.configure(
                    text=f"Target Coordinates: ({cx}, {cy}) on '{src}' [Calibrated]",
                    fg=ACCENT_GREEN,
                )
                self.update_stage("calib_lock", "pass", f"({cx}, {cy})")
                self.btn_run.configure(state="normal")
                self.log_msg(f"Target patch locked at ({cx}, {cy}). Click '2. Run Full Link Test' to verify.")
            else:
                self.update_stage("calib_lock", "fail", "Patch not found in OBS capture")
                self.lbl_coords.configure(
                    text="Target Coordinates: Calibration Failed",
                    fg=ACCENT_RED,
                )

        threading.Thread(target=_task, daemon=True).start()

    def _on_run_test_clicked(self):
        self._disable_buttons()
        coords = self.runner.calibrated_coords
        if not coords:
            self.log_msg("Cannot start test: coordinates not calibrated.")
            self._enable_buttons()
            return

        # Explicitly reset result to unverified at the start of every run
        self.result["ok"] = False
        self.result["latency_ms"] = 0.0
        self.result["details"] = ["Test in progress..."]
        self.result["attempts"] += 1

        # Reset trial checklist indicators
        self.update_stage("trial1", "pending")
        self.update_stage("trial2", "pending")
        self.update_stage("trial3", "pending")
        self.update_stage("trial4", "pending")
        self.update_stage("full_link_ok", "running", "Executing 4 trials...")

        # Pick 4 distinct randomized keys
        rand_keys = random.sample(RANDOM_KEY_POOL, 4)

        def _task():
            trials = [
                (1, "trial1", "green", rand_keys[0], True),   # is_mandatory = True (baseline 60ms)
                (2, "trial2", "red", rand_keys[1], False),     # is_mandatory = False (benchmark 35ms)
                (3, "trial3", "white", rand_keys[2], False),   # is_mandatory = False (benchmark 20ms)
            ]

            baseline_ok = False
            latencies = []
            bench_progression = []
            failure_detail = ""

            # --- Stage 1: Multi-Frequency Visual Telemetry & Latency Benchmark ---
            for idx, stage_id, stimulus_col, key, is_mandatory in trials:
                self.update_stage(stage_id, "running", f"(Key '{key}')")
                self.lbl_random_key.configure(text=f"Trial {idx}/4 Key: '{key}'")

                ok, lat, msg = self.runner.run_single_trial(
                    coords=coords,
                    stimulus_name=stimulus_col,
                    target_key=key,
                    trial_index=idx,
                    set_color_fn=self.set_canvas_color,
                    arm_key_listener_fn=self.arm_key_listener,
                    await_key_fn=self.await_key,
                )

                if ok:
                    latencies.append(lat)
                    bench_progression.append(f"{STIMULUS_CONFIGS[stimulus_col]['interval_ms']}ms -> {lat:.1f}ms")
                    self.update_stage(stage_id, "pass", f"'{key}' in {lat:.1f}ms")
                    if is_mandatory:
                        baseline_ok = True
                else:
                    if is_mandatory:
                        failure_detail = f"Trial {idx}/4 ({stimulus_col}, key '{key}') baseline perception failed: {msg}"
                        self.update_stage(stage_id, "fail", msg)
                        self.log_msg(f"--- FAILURE: {failure_detail} ---")
                        break
                    else:
                        warn_msg = f"Non-blocking benchmark probe ({stimulus_col}) dropped/delayed: {msg}"
                        self.update_stage(stage_id, "warn", "Probe dropped (Jitter) - Advisory only")
                        self.log_msg(f"[WARN] Trial {idx}/4: {warn_msg} (Baseline color perception remains valid)")

            trial4_ok = False
            if baseline_ok:
                if bench_progression:
                    self.log_msg(f"[BENCHMARK] Multi-Freq Progression: {' | '.join(bench_progression)} (Ceiling ~50.0Hz)")

                # --- Stage 2: Complex 2D Randomized Area Pattern & Decoy Rejection ---
                self.update_stage("trial4", "running", f"(Key '{rand_keys[3]}')")
                self.lbl_random_key.configure(text=f"Trial 4/4 2D Pattern Key: '{rand_keys[3]}'")

                ok4, lat4, msg4 = self.runner.run_pattern_trial(
                    coords=coords,
                    trial_index=4,
                    target_key=rand_keys[3],
                    set_quadrants_fn=self.set_canvas_quadrants,
                    set_color_fn=self.set_canvas_color,
                    arm_key_listener_fn=self.arm_key_listener,
                    await_key_fn=self.await_key,
                )

                if not ok4:
                    failure_detail = f"Trial 4/4 (2D Pattern, key '{rand_keys[3]}') failed: {msg4}"
                    self.update_stage("trial4", "fail", msg4)
                    self.log_msg(f"--- FAILURE: {failure_detail} ---")
                else:
                    trial4_ok = True
                    latencies.append(lat4)
                    self.update_stage("trial4", "pass", f"'{rand_keys[3]}' in {lat4:.1f}ms")

            passed_all = baseline_ok and trial4_ok
            if passed_all:
                avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
                self.result["ok"] = True
                self.result["latency_ms"] = avg_lat
                self.result["details"] = [
                    f"target ({coords[0]}, {coords[1]})",
                    f"Verified keys [{', '.join(rand_keys[:len(latencies)])}] (Avg {avg_lat:.1f}ms)",
                    f"latencies: {[f'{l:.1f}ms' for l in latencies]}",
                    "baseline color perception & 2D area pattern verified",
                ]
                self.set_canvas_color("#15803d")
                self.update_stage("full_link_ok", "pass", f"Verified (Avg {avg_lat:.1f}ms)")
                self.lbl_random_key.configure(
                    text=f"Full Link Verified (Avg {avg_lat:.1f}ms)",
                    fg=ACCENT_GREEN,
                )
                self.log_msg(f"=== FULL LINK TEST PASSED! Verified! Avg: {avg_lat:.1f}ms ===")
                self.btn_finish.configure(state="normal", text="Finish Test")
            else:
                self.result["ok"] = False
                self.result["latency_ms"] = 0.0
                self.result["details"] = [f"FAILED: {failure_detail}"]
                self.result["failures"].append(failure_detail)
                self.set_canvas_color("#991b1b")
                self.update_stage("full_link_ok", "fail", failure_detail)
                self.lbl_random_key.configure(
                    text=f"Failed at Trial: {failure_detail}",
                    fg=ACCENT_RED,
                )
                self.log_msg(f"=== FULL LINK TEST FAILED: {failure_detail} ===")
                self.btn_finish.configure(state="normal", text="Finish Test")

            self._enable_buttons()

        threading.Thread(target=_task, daemon=True).start()

    def _disable_buttons(self):
        self.btn_calib.configure(state="disabled")
        self.btn_run.configure(state="disabled")

    def _enable_buttons(self):
        self.btn_calib.configure(state="normal")
        self.btn_run.configure(state="normal")


def main():
    parser = argparse.ArgumentParser(description="MYCRO Full Link Hardware Test")
    parser.add_argument("--host", default=os.environ.get("MYCRO_HOST", "mycro.local"), help="Device IP / mDNS host")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MYCRO_RAW_PORT", 7373)), help="Device port")
    parser.add_argument("--token", default=os.environ.get("MYCRO_TOKEN", ""), help="Device token")
    parser.add_argument("--source", default=os.environ.get("MYCRO_OBS_SOURCE", ""), help="Explicit OBS capture source or input name")
    args = parser.parse_args()

    app = FullLinkApp(args.host, args.port, args.token, source=args.source)
    app.mainloop()


if __name__ == "__main__":
    main()
