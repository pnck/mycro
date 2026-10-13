"""On-device template matching (Phase 3): row-convolution NCC on ulab.

Dual-import: ulab.numpy on CircuitPython, numpy on CPython (unit tests
only). The core was validated on-device by tests/device/bench_device.py
(exact argmax on planted templates; 80x60/T=16 runs in ~570 ms).

Luma: gray = (r*77 + g*150 + b*29) >> 8 — integer-only BT.601. The OBS
plugin (obs/obs_mycro.py) and tests/device/fake_capture.py replicate
this EXACTLY so grabbed templates and match frames share a color space.
"""

try:
    import ulab.numpy as np
except ImportError:  # CPython unit tests
    import numpy as np


def rgb_to_gray(data):
    """bytes RGB888 -> bytes gray8 (integer BT.601, see module docstring)."""
    out = bytearray(len(data) // 3)
    j = 0
    for i in range(0, len(data), 3):
        out[j] = (data[i] * 77 + data[i + 1] * 150 + data[i + 2] * 29) >> 8
        j += 1
    return bytes(out)


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


def _boxsum(frame, kh, kw):
    """Sum of kh-by-kw windows, separable via 1D convolutions."""
    H, W = frame.shape
    rows = np.zeros((H, W - kw + 1))
    ones_w = np.ones(kw)
    for y in range(H):
        c = np.convolve(frame[y], ones_w)
        rows[y] = c[kw - 1:kw - 1 + (W - kw + 1)]
    rt = rows.transpose()
    ones_h = np.ones(kh)
    outt = np.zeros((W - kw + 1, H - kh + 1))
    for x in range(W - kw + 1):
        c = np.convolve(rt[x], ones_h)
        outt[x] = c[kh - 1:kh - 1 + (H - kh + 1)]
    return outt.transpose()


def ncc_match(frame_bytes, fw, fh, tpl_bytes, tw, th):
    """Locate a gray8 template in a gray8 frame.

    Returns (x, y, score) with score in permille (1000 = perfect match).
    A flat template (zero variance) has no texture to match and returns
    score 0."""
    frame = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((fh, fw)) * 1.0
    tmpl = np.frombuffer(tpl_bytes, dtype=np.uint8).reshape((th, tw)) * 1.0
    n = tw * th
    tc = tmpl - np.mean(tmpl)
    tnorm = float(np.sqrt(np.sum(tc * tc)))
    if tnorm <= 0.0:
        return 0, 0, 0
    corr = _xcorr2(frame, tc)
    wsum = _boxsum(frame, th, tw)
    wsum2 = _boxsum(frame * frame, th, tw)
    var = wsum2 - wsum * wsum / n
    wnorm = np.sqrt(np.maximum(var, 0.0))
    ncc_map = corr / (wnorm * tnorm + 1e-6)
    flat = ncc_map.flatten()
    idx = int(np.argmax(flat))
    ow = ncc_map.shape[1]
    score = int(float(flat[idx]) * 1000)
    if score > 1000:  # fp overshoot on exact matches
        score = 1000
    elif score < -1000:
        score = -1000
    return idx % ow, idx // ow, score
