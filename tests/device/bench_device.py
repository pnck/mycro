"""MYCRO on-device Phase 3 benchmark (CircuitPython only — NOT pytest).

Measures the numbers the Phase 3 ADR needs, on real hardware:

    B1  memory baseline + frame-buffer cost (PSRAM watermark)
    B2  gray-frame ingest: raw bytes -> ulab float array
    B3  template matching via row-wise ulab convolve NCC, swept over
        frame/template sizes; argmax position is verified against the
        planted template, so the bench doubles as the algorithm spike

Run: copy this file to the CIRCUITPY root (deploy.sh exact-set sync will
remove it again — re-copy after deploying), then at the serial REPL:

    import bench_device
    bench_device.run()

Paste the BENCH lines back. Self-contained; touches no project files.
"""

import gc
import time

import ulab.numpy as np

CONFIGS = (
    # (frame_h, frame_w, template_size)
    (120, 160, 8),
    (120, 160, 16),
    (120, 160, 32),
    (60, 80, 16),
)


def _mem(tag):
    gc.collect()
    print("BENCH mem %-18s free=%d alloc=%d" % (tag, gc.mem_free(), gc.mem_alloc()))


def _flip(v):
    try:
        return np.flip(v)
    except Exception:
        return np.array([v[i] for i in range(len(v) - 1, -1, -1)])


def _xcorr2(frame, tmpl):
    """Cross-correlate frame with tmpl via 1D convolutions per template
    row. Python-level iterations: TH * (H-TH+1), all heavy math in C."""
    H, W = frame.shape
    TH, TW = tmpl.shape
    OH, OW = H - TH + 1, W - TW + 1
    acc = np.zeros((OH, OW))
    for r in range(TH):
        k = _flip(tmpl[r])
        for y in range(OH):
            c = np.convolve(frame[y + r], k)
            acc[y] += c[TW - 1:TW - 1 + OW]
    return acc


def _boxsum(frame, t):
    """Sum of t-by-t windows, separable via 1D convolutions."""
    H, W = frame.shape
    ones = np.ones(t)
    rows = np.zeros((H, W - t + 1))
    for y in range(H):
        c = np.convolve(frame[y], ones)
        rows[y] = c[t - 1:t - 1 + (W - t + 1)]
    rt = rows.transpose()
    outt = np.zeros((W - t + 1, H - t + 1))
    for x in range(W - t + 1):
        c = np.convolve(rt[x], ones)
        outt[x] = c[t - 1:t - 1 + (H - t + 1)]
    return outt.transpose()


def ncc(frame, tmpl):
    """Normalized cross-correlation map; returns (map, (dy, dx))."""
    TH, TW = tmpl.shape
    n = TH * TW
    tc = tmpl - np.mean(tmpl)
    tnorm = float(np.sqrt(np.sum(tc * tc)))
    corr = _xcorr2(frame, tc)
    wsum = _boxsum(frame, TH)
    wsum2 = _boxsum(frame * frame, TH)
    var = wsum2 - wsum * wsum / n
    wnorm = np.sqrt(np.maximum(var, 0.0))
    ncc_map = corr / (wnorm * tnorm + 1e-6)
    flat = ncc_map.flatten()
    idx = int(np.argmax(flat))
    ow = ncc_map.shape[1]
    return ncc_map, (idx // ow, idx % ow)


def _make_frame(h, w, t, plant):
    """Deterministic gray bytes with the template planted at `plant`
    (dy, dx) so argmax correctness is checkable."""
    py, px = plant
    data = bytearray(h * w)
    i = 0
    for y in range(h):
        for x in range(w):
            v = (x * 7 + y * 13) % 256
            if py <= y < py + t and px <= x < px + t:
                v = ((x - px) * 5 + (y - py) * 11 + 40) % 256
            data[i] = v
            i += 1
    tmpl = [[float((x * 5 + y * 11 + 40) % 256) for x in range(t)]
            for y in range(t)]
    return bytes(data), np.array(tmpl)


def _bench_config(h, w, t):
    plant = (h // 2, w // 2)
    raw, tmpl = _make_frame(h, w, t, plant)

    t0 = time.monotonic_ns()
    frame = np.frombuffer(raw, dtype=np.uint8).reshape((h, w)) * 1.0
    ingest_ms = (time.monotonic_ns() - t0) / 1e6

    gc.collect()
    t0 = time.monotonic_ns()
    ncc_map, pos = ncc(frame, tmpl)
    ncc_ms = (time.monotonic_ns() - t0) / 1e6

    print("BENCH ncc %dx%d T=%-2d ingest=%.0fms ncc=%.0fms argmax=%s "
          "expect=%s match=%s" % (
              w, h, t, ingest_ms, ncc_ms, pos, plant,
              "YES" if pos == plant else "NO"))
    _mem("after %dx%d T=%d" % (w, h, t))
    return ncc_map


def run():
    _mem("baseline")
    for h, w, t in CONFIGS:
        try:
            _bench_config(h, w, t)
        except Exception as e:
            print("BENCH ncc %dx%d T=%d FAILED: %s: %s"
                  % (w, h, t, type(e).__name__, e))
    _mem("final")
    print("BENCH done")
