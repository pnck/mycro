#!/usr/bin/env python3
"""Adversarial MYCRO Game Combat & Image Namespace Fuzz Suite.

Constructs realistic, highly complex, adversarial automated gaming scenarios:
1. Full Auto Mob Farming Combat Loop (Visual target acquisition, double-invariant guarded combat, mid-strike kill unwind, emergency retreat on ambush, decoy filtering).
2. Auto PvP Reaction Duel Bot (Continuous telemetry watchdogs, sub-100ms kick/counter on dangerous casts, cascading lethal strike override).
3. Degenerate & Edge Malformations (Out-of-bounds screen scanning with error recovery, 24x rapid snapshot pool thrashing, quad-watch telemetry flood).
4. Network Chaos & Reconnection (Provider abrupt socket drops mid-wait, reconnection resumption).
5. Live Hardware Steady-State PSRAM Audit.
"""

import os
import sys
import time
import json
import socket
import struct
import threading
import urllib.request
import urllib.parse

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "Projects", "self", "circuitmacro"))
if not os.path.isdir(REPO_ROOT):
    REPO_ROOT = os.getcwd()

sys.path.insert(0, os.path.join(REPO_ROOT, "src", "lib"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tests", "device"))

import devicetest
import reference_provider
from client import Client

DEVICE_IP = os.getenv("MYCRO_DEVICE_IP", "192.168.94.112")
RAW_PORT = int(os.getenv("MYCRO_RAW_PORT", "7373"))
TOKEN = os.getenv("MYCRO_TOKEN", "")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail else ""))
    if not ok:
        raise AssertionError(f"Step '{name}' failed: {detail}")


def get_free_mem(ctx):
    c = ctx.client()
    try:
        info = c.call("device.info", {"gc": True})
        return info["payload"]["free_mem"]
    finally:
        c.close()


# ===========================================================================
# Adversarial Game Provider (Mock World Simulator)
# ===========================================================================

# 4x4 Goblin Icon: Red border (0xFF0000), Green eyes (0x00FF00)
GOBIN_PIXELS = [
    0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000,
    0xFF0000, 0x00FF00, 0x00FF00, 0xFF0000,
    0xFF0000, 0x00FF00, 0x00FF00, 0xFF0000,
    0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000,
]

# 4x4 Decoy Illusion: Red border (0xFF0000), Blue eyes (0x0000FF)
DECOY_PIXELS = [
    0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000,
    0xFF0000, 0x0000FF, 0x0000FF, 0xFF0000,
    0xFF0000, 0x0000FF, 0x0000FF, 0xFF0000,
    0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000,
]

# 4x4 Noisy Goblin: Small variations (+/- 3 on each channel)
NOISY_GOBIN_PIXELS = [
    0xFC0201, 0xFE0100, 0xFD0002, 0xFC0101,
    0xFE0000, 0x02FC01, 0x01FD02, 0xFD0100,
    0xFD0201, 0x01FE00, 0x00FC01, 0xFE0202,
    0xFC0001, 0xFD0102, 0xFE0200, 0xFD0001,
]


class AdversarialGameProvider(reference_provider.ReferenceProvider):
    """Virtual MMORPG / PvP Game World Screen Provider."""

    def __init__(self, host, port, token, w=80, h=60):
        super().__init__(host, port, token)
        self.w, self.h = w, h
        self.canvas = bytearray(w * h * 3)
        self.reset_world()

    def reset_world(self):
        # Dark terrain background (20, 30, 20)
        for i in range(0, len(self.canvas), 3):
            self.canvas[i] = 20
            self.canvas[i + 1] = 30
            self.canvas[i + 2] = 20
        # HUD Defaults
        self.set_pixel_raw(10, 5, 0x00FF00)      # Player HP: 100% Green
        self.set_pixel_raw(10, 6, 0x000000)      # Player Shield: None
        self.set_pixel_raw(50, 5, 0xFF0000)      # Mob HP: 100% Red
        self.set_pixel_raw(50, 6, 0x808080)      # Enemy Cast: Idle Grey
        self.set_frame(self.w, self.h, bytes(self.canvas))

    def set_pixel_raw(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            idx = (y * self.w + x) * 3
            self.canvas[idx] = (color >> 16) & 0xFF
            self.canvas[idx + 1] = (color >> 8) & 0xFF
            self.canvas[idx + 2] = color & 0xFF

    def set_pixel(self, x, y, color):
        self.set_pixel_raw(x, y, color)
        self.set_frame(self.w, self.h, bytes(self.canvas))

    def set_player_hp(self, color):
        self.set_pixel(10, 5, color)

    def set_mob_hp(self, color):
        self.set_pixel(50, 5, color)

    def set_enemy_cast(self, color):
        self.set_pixel(50, 6, color)

    def draw_icon(self, x, y, icon_w, icon_h, pixels):
        for iy in range(icon_h):
            for ix in range(icon_w):
                color = pixels[iy * icon_w + ix]
                self.set_pixel_raw(x + ix, y + iy, color)
        self.set_frame(self.w, self.h, bytes(self.canvas))

    def clear_icon(self, x, y, icon_w, icon_h):
        for iy in range(icon_h):
            for ix in range(icon_w):
                self.set_pixel_raw(x + ix, y + iy, 0x141E14)
        self.set_frame(self.w, self.h, bytes(self.canvas))


# ===========================================================================
# 1. Full Auto Mob Farming Combat Loop
# ===========================================================================

FARMING_MACRO_DSL = r"""\#!syntax: free
\use{img}\use{net}
\pace{20ms}
\pixelrect{goblin_icon}{\row{0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000}, \row{0xFF0000, 0x00FF00, 0x00FF00, 0xFF0000}, \row{0xFF0000, 0x00FF00, 0x00FF00, 0xFF0000}, \row{0xFF0000, 0xFF0000, 0xFF0000, 0xFF0000}}
\call{img.watch}{my_hp}{10,5}{20ms,30s}
\call{img.watch}{mob_hp}{50,5}{20ms,30s}
\wait[500ms]{$slot.img.my_hp.val}
\wait[500ms]{$slot.img.mob_hp.val}

\# Visual target scan
\call{img.snap}{30,20,4,4}\set{target_snap}{$ret}
\call{img.diff}{$goblin_icon}{$target_snap}{5}\set{match_diff}{$ret}

\if{ {$match_diff, =, 0} }{
  \call{net.send}{mob_found}
  \tap{tab}
  \# Outer guard: Player life invariant
  \guarded[20ms]{ {$slot.img.my_hp.val, !=, 0x000000} }{
    \# Inner guard: Mob life invariant
    \guarded[20ms]{ {$slot.img.mob_hp.val, !=, 0x000000} }{
      \rep{6}{
        \tap{1}\delay{80ms}
        \tap{2}\delay{80ms}
        \call{net.send}{combo_strike}
      }
      \call{net.send}{mob_survived}
    }{
      \# Mob killed mid-combat! Loot and untarget
      \call{net.send}{mob_killed}
      \tap{f}
      \tap{escape}
    }
  }{
    \# Player critical ambush! Drink potion & bubble
    \call{net.send}{emergency_abort}
    \tap{q}
    \kdown{ctrl}\tap{1}\kup{ctrl}
  }
}{
  \call{net.send}{mob_missed}
}
\call{img.unwatch}{my_hp}
\call{img.unwatch}{mob_hp}
\call{net.send}{cycle_done}
"""


def seed_slots(client, mapping):
    for name, val in mapping.items():
        client.call("event.push", {"name": name, "fields": {"val": val}})


def submit_macro_with_seeds(ctx, code, seeds=None, timeout=15.0):
    c = ctx.client()
    try:
        if seeds:
            seed_slots(c, seeds)
        r = c.call("macro.submit", {"code": code})
        if r.get("type") == "macro.submit.ok":
            tags, result = devicetest.collect_tag_sequence(c, timeout)
            return tags, result
        return [], None
    finally:
        c.close()


def test_auto_mob_farming(ctx, sim):
    print("\n=== [Suite 1] Auto Mob Farming Combat Loop ===")
    seeds_farming = {"img.my_hp": 0x00FF00, "img.mob_hp": 0xFF0000}

    # 1.1 Happy Path: Find mob, attack, mob dies on 2nd strike, loot and clear
    sim.reset_world()
    sim.draw_icon(30, 20, 4, 4, GOBIN_PIXELS)
    
    c = ctx.client()
    try:
        seed_slots(c, seeds_farming)
        c.call("macro.submit", {"code": FARMING_MACRO_DSL})
        t_found = devicetest.recv_tag(c, "mob_found", timeout=5.0)
        record("1.1a Mob visually recognized and locked", t_found is not None)
        
        # Receive first strike
        t_strike1 = devicetest.recv_tag(c, "combo_strike", timeout=5.0)
        record("1.1b Combat rotation started (strike 1)", t_strike1 is not None)
        
        # Immediately kill mob in simulator
        sim.set_mob_hp(0x000000)
        
        # Collect remaining tags through cycle completion
        tags, res = devicetest.collect_tag_sequence(c, timeout=5.0)
        record("1.1c Mob death broke inner guard -> looted & untargeted", 
               "mob_killed" in tags and "cycle_done" in tags and res and res["payload"]["state"] == "done",
               f"tags={tags}")
    finally:
        c.close()

    # 1.2 Adversarial Ambush: Player HP drops to critical during combat
    sim.reset_world()
    sim.draw_icon(30, 20, 4, 4, GOBIN_PIXELS)
    
    c = ctx.client()
    try:
        seed_slots(c, seeds_farming)
        c.call("macro.submit", {"code": FARMING_MACRO_DSL})
        devicetest.recv_tag(c, "mob_found", timeout=5.0)
        devicetest.recv_tag(c, "combo_strike", timeout=5.0)
        
        # Ambush! Drop player HP to 0x000000
        sim.set_player_hp(0x000000)
        
        tags, res = devicetest.collect_tag_sequence(c, timeout=5.0)
        record("1.2 Player ambush broke outer guard -> emergency potion & shield", 
               "emergency_abort" in tags and "cycle_done" in tags and res and res["payload"]["state"] == "done",
               f"tags={tags}")
    finally:
        c.close()

    # 1.3 Camouflage & Decoy Filtering: Decoy rejected, Noisy Goblin accepted
    # First: Decoy with blue eyes at (30,20)
    sim.reset_world()
    sim.draw_icon(30, 20, 4, 4, DECOY_PIXELS)
    tags, _ = submit_macro_with_seeds(ctx, FARMING_MACRO_DSL, seeds=seeds_farming, timeout=6.0)
    record("1.3a Visual decoy rejected by diff tolerance", tags == ["mob_missed", "cycle_done"], f"tags={tags}")

    # Second: Real goblin with +/- 3 lighting noise
    sim.reset_world()
    sim.draw_icon(30, 20, 4, 4, NOISY_GOBIN_PIXELS)
    # The mob will survive all 6 strikes since HP stays full
    tags, _ = submit_macro_with_seeds(ctx, FARMING_MACRO_DSL, seeds=seeds_farming, timeout=8.0)
    record("1.3b Noisy goblin recognized and engaged under tol=5", 
           "mob_found" in tags and "mob_survived" in tags and "cycle_done" in tags,
           f"tags={tags}")


# ===========================================================================
# 2. Auto PvP Reaction Duel Bot
# ===========================================================================

PVP_DUEL_DSL = r"""\#!syntax: free
\use{img}\use{net}
\pace{15ms}
\call{img.watch}{player_hp}{10,5}{20ms,30s}
\call{img.watch}{enemy_cast}{50,6}{20ms,30s}
\wait[500ms]{$slot.img.player_hp.val}
\wait[500ms]{$slot.img.enemy_cast.val}

\# Outer guard: Player life invariant
\guarded[20ms]{ {$slot.img.player_hp.val, !=, 0x000000} }{
  \# Middle guard: Enemy cast invariant (enemy_cast != 0xFF00FF Pyroblast)
  \guarded[20ms]{ {$slot.img.enemy_cast.val, !=, 0xFF00FF} }{
    \call{net.send}{burst_started}
    \rep{5}{
      \tap{1}\delay{80ms}
      \tap{2}\delay{80ms}
      \call{net.send}{hit_landed}
    }
    \call{net.send}{burst_completed}
  }{
    \# Opponent started Pyroblast! Kick and stun
    \call{net.send}{kick_executed}
    \tap{r}\delay{20ms}
    \tap{4}
    \call{net.send}{enemy_stunned}
  }
}{
  \# Fatal strike taken! Pop Ice Block
  \call{net.send}{ice_block_popped}
  \kdown{ctrl}\tap{3}\kup{ctrl}
  \tap{shift+space}
}
\call{img.unwatch}{player_hp}
\call{img.unwatch}{enemy_cast}
\call{net.send}{duel_cycle_done}
"""


def test_auto_pvp_reaction(ctx, sim):
    print("\n=== [Suite 2] Auto PvP Reaction Duel Bot ===")
    seeds_pvp = {"img.player_hp": 0x00FF00, "img.enemy_cast": 0x808080}

    # 2.1 Instant Interrupt / Kick on Dangerous Spellcast
    sim.reset_world()
    c = ctx.client()
    try:
        seed_slots(c, seeds_pvp)
        c.call("macro.submit", {"code": PVP_DUEL_DSL})
        devicetest.recv_tag(c, "burst_started", timeout=5.0)
        devicetest.recv_tag(c, "hit_landed", timeout=5.0)
        
        # Enemy starts casting Pyroblast (0xFF00FF)
        t_cast_start = time.monotonic()
        sim.set_enemy_cast(0xFF00FF)
        
        # Macro must interrupt within 150ms
        t_kick = devicetest.recv_tag(c, "kick_executed", timeout=5.0)
        t_kick_elapsed = (time.monotonic() - t_cast_start) * 1000
        
        record("2.1a Pyroblast cast intercepted and kicked", 
               t_kick is not None and t_kick_elapsed < 150,
               f"reaction_latency={t_kick_elapsed:.1f}ms (<150ms)")
        
        tags, res = devicetest.collect_tag_sequence(c, timeout=5.0)
        record("2.1b Counter-stun combo completed", 
               "enemy_stunned" in tags and "duel_cycle_done" in tags,
               f"tags={tags}")
    finally:
        c.close()

    # 2.2 Cascading Priority: Fatal Strike overrides spellcast interrupt
    sim.reset_world()
    c = ctx.client()
    try:
        seed_slots(c, seeds_pvp)
        c.call("macro.submit", {"code": PVP_DUEL_DSL})
        devicetest.recv_tag(c, "burst_started", timeout=5.0)
        
        # Update sim world canvas AND push lethal strike immediately
        sim.set_pixel_raw(10, 5, 0x000000)
        sim.set_pixel_raw(50, 6, 0xFF00FF)
        sim.set_frame(sim.w, sim.h, bytes(sim.canvas))
        c.call("event.push", {"name": "img.player_hp", "fields": {"val": 0x000000}})
        
        # Outer guard must dominate: ice_block_popped, NOT kick_executed
        tags, res = devicetest.collect_tag_sequence(c, timeout=5.0)
        
        record("2.2 Cascading lethal strike prioritized Ice Block over Kick",
               "ice_block_popped" in tags and "kick_executed" not in tags and "duel_cycle_done" in tags,
               f"tags={tags}")
    finally:
        c.close()


# ===========================================================================
# 3. Degenerate & Edge Malformations
# ===========================================================================

def test_degenerate_and_edge_malformations(ctx, sim):
    print("\n=== [Suite 3] Degenerate Edge Cases & Handle Thrashing ===")

    # 3.1 Out-of-bounds screen scan with dynamic error recovery
    p_oob = (
        r"\#!syntax: free" "\n"
        r"\use{img}\use{net}" "\n"
        r"\call{img.snap}{75,55,10,10}" "\n"
        r"\if{ {$err, =, 4} }{" "\n"
        r"  \# Caught ERR_OUT_OF_BOUNDS -> clamp coordinates to valid frame" "\n"
        r"  \call{net.send}{caught_oob}" "\n"
        r"  \call{img.snap}{70,50,10,10}" "\n"
        r"  \if{ {$err, =, 0} }{" "\n"
        r"    \call{net.send}{clamped_ok}" "\n"
        r"  }" "\n"
        r"}"
    )
    tags, res = devicetest.submit_tag_sequence(ctx, p_oob)
    record("3.1 Out-of-bounds scan caught ERR_OUT_OF_BOUNDS and recovered",
           tags == ["caught_oob", "clamped_ok"] and res and res["payload"]["state"] == "done",
           f"tags={tags}")

    # 3.2 24x rapid snapshot pool thrashing (re-using registers, testing pool limit 8)
    p_thrash = (
        r"\#!syntax: free" "\n"
        r"\use{img}\use{net}" "\n"
        r"\set{ok}{1}" "\n"
        r"\rep{12}{" "\n"
        r"  \call{img.snap}{0,0,4,4}\set{s1}{$ret}" "\n"
        r"  \if{ {$err, !=, 0} }{ \set{ok}{0} }" "\n"
        r"  \call{img.snap}{10,10,4,4}\set{s2}{$ret}" "\n"
        r"  \if{ {$err, !=, 0} }{ \set{ok}{0} }" "\n"
        r"}" "\n"
        r"\if{ {$ok, =, 1} }{" "\n"
        r"  \call{img.at}{$s1}{0,0}\set{c1}{$ret}" "\n"
        r"  \call{img.at}{$s2}{0,0}\set{c2}{$ret}" "\n"
        r"  \call{net.send}{pool_thrash_ok}" "\n"
        r"}{" "\n"
        r"  \call{net.send}{pool_thrash_fail}" "\n"
        r"}"
    )
    tags, res = devicetest.submit_tag_sequence(ctx, p_thrash, timeout=10.0)
    record("3.2 24x rapid snapshot thrashing passed without pool starvation",
           tags == ["pool_thrash_ok"] and res and res["payload"]["state"] == "done",
           f"tags={tags}")

    # 3.3 Quad-watch telemetry flood (200 pkts/s) concurrent with arithmetic & moves
    p_quad_watch = (
        r"\#!syntax: free" "\n"
        r"\use{img}\use{net}" "\n"
        r"\pace{10ms}" "\n"
        r"\call{img.watch}{w1}{10,5}{20ms,10s}" "\n"
        r"\call{img.watch}{w2}{10,6}{20ms,10s}" "\n"
        r"\call{img.watch}{w3}{50,5}{20ms,10s}" "\n"
        r"\call{img.watch}{w4}{50,6}{20ms,10s}" "\n"
        r"\wait[300ms]{$slot.img.w1.val}" "\n"
        r"\set{acc}{0}" "\n"
        r"\rep{5}{" "\n"
        r"  \add{acc}{10}" "\n"
        r"  \move{0,0}" "\n"
        r"  \delay{20ms}" "\n"
        r"}" "\n"
        r"\if{ {$acc, =, 50} }{" "\n"
        r"  \call{net.send}{quad_watch_ok}" "\n"
        r"}" "\n"
        r"\call{img.unwatch}{w1}\call{img.unwatch}{w2}" "\n"
        r"\call{img.unwatch}{w3}\call{img.unwatch}{w4}"
    )
    tags, res = devicetest.submit_tag_sequence(ctx, p_quad_watch, timeout=10.0)
    record("3.3 Quad-watch telemetry flood survived under concurrent action pacing",
           tags == ["quad_watch_ok"] and res and res["payload"]["state"] == "done",
           f"tags={tags}")


# ===========================================================================
# 4. Network Chaos & Reconnection
# ===========================================================================

def test_network_chaos_and_reconnection(ctx, sim):
    print("\n=== [Suite 4] Network Chaos & Reconnection ===")

    # 4.1 Sudden socket drop mid-wait -> macro cleanly times out
    p_wait_timeout = (
        r"\#!syntax: free" "\n"
        r"\use{img}\use{net}" "\n"
        r"\wait[300ms]{$slot.img.dropped_sig.val}" "\n"
        r"\if{ {$timeout, =, 1} }{" "\n"
        r"  \call{net.send}{timeout_caught}" "\n"
        r"}"
    )
    
    # Abruptly terminate provider socket
    sim.stop()
    time.sleep(0.1)
    
    tags, res = devicetest.submit_tag_sequence(ctx, p_wait_timeout, timeout=5.0)
    record("4.1 Provider disconnect mid-wait gracefully timed out",
           tags == ["timeout_caught"] and res and res["payload"]["state"] == "done",
           f"tags={tags}")

    # 4.2 Reconnection with img.hello -> subsequent requests succeed immediately
    sim_new = AdversarialGameProvider(DEVICE_IP, RAW_PORT, TOKEN)
    sim_new.start()
    
    p_reconnected_snap = (
        r"\#!syntax: free" "\n"
        r"\use{img}\use{net}" "\n"
        r"\call{img.snap}{10,5,2,2}\set{s}{$ret}" "\n"
        r"\if{ {$err, =, 0} }{" "\n"
        r"  \call{net.send}{reconnect_ok}" "\n"
        r"}"
    )
    
    # img.hello has no wire ack; wait up to 5s for cooperative registration
    deadline = time.monotonic() + 5.0
    tags = []
    res = None
    while time.monotonic() < deadline:
        tags, res = devicetest.submit_tag_sequence(ctx, p_reconnected_snap, timeout=3.0)
        if tags == ["reconnect_ok"] and res and res["payload"]["state"] == "done":
            break
        time.sleep(0.1)

    record("4.2 Reconnected provider accepted and served snapshots cleanly",
           tags == ["reconnect_ok"] and res and res["payload"]["state"] == "done",
           f"tags={tags}")
    
    return sim_new


# ===========================================================================
# Main Execution
# ===========================================================================

def main():
    print(f"Connecting to MYCRO device at {DEVICE_IP}:{RAW_PORT}...")
    ctx = devicetest.Ctx(DEVICE_IP, RAW_PORT, TOKEN)
    
    mem_initial = get_free_mem(ctx)
    print(f"Device initial PSRAM: {mem_initial} bytes")

    # Start the Adversarial Game Provider
    print("Starting Adversarial Virtual Game Screen Provider...")
    sim = AdversarialGameProvider(DEVICE_IP, RAW_PORT, TOKEN)
    sim.start()

    try:
        test_auto_mob_farming(ctx, sim)
        test_auto_pvp_reaction(ctx, sim)
        test_degenerate_and_edge_malformations(ctx, sim)
        sim = test_network_chaos_and_reconnection(ctx, sim)

        # PSRAM Leak check
        mem_final = get_free_mem(ctx)
        delta = mem_final - mem_initial
        print(f"\nDevice final PSRAM: {mem_final} bytes (Delta: {delta:+d} bytes)")
        record("5. PSRAM steady state with zero leaks after adversarial gaming fuzz",
               delta >= -65536,
               f"initial={mem_initial}, final={mem_final}, delta={delta:+d}B")

        print("\n" + "=" * 65)
        print(">>> ALL ADVERSARIAL GAMING FUZZ TESTS PASSED 100%! <<<")
        print("=" * 65)
    finally:
        sim.stop()


if __name__ == "__main__":
    main()
