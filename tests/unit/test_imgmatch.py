"""Unit tests for src/lib/imgmatch.py (numpy standing in for ulab).

The synthetic frame mirrors tests/device/bench_device.py: a deterministic
base pattern with the template planted at a known position, so an exact
argmax hit is a correctness check, not just a smoke test.
"""

import imgmatch

FW, FH = 80, 60
PLANT = (30, 22)  # (x, y)
TS = 16


def _make_frame():
    data = bytearray(FW * FH)
    i = 0
    for y in range(FH):
        for x in range(FW):
            v = (x * 7 + y * 13) % 256
            px, py = PLANT
            if py <= y < py + TS and px <= x < px + TS:
                v = ((x - px) * 5 + (y - py) * 11 + 40) % 256
            data[i] = v
            i += 1
    return bytes(data)


def _make_tpl():
    return bytes(((x * 5 + y * 11 + 40) % 256) for y in range(TS) for x in range(TS))


def test_exact_hit():
    x, y, score = imgmatch.ncc_match(_make_frame(), FW, FH, _make_tpl(), TS, TS)
    assert (x, y) == PLANT
    assert score >= 990


def test_score_bounded_and_lower_on_miss():
    # nonlinear pattern with no planted patch: best score must be
    # meaningfully below the exact-hit score
    frame = bytes(((x * x + y * y * 3 + 7) % 256) for y in range(FH) for x in range(FW))
    _, _, score = imgmatch.ncc_match(frame, FW, FH, _make_tpl(), TS, TS)
    assert -1000 <= score < 990


def test_flat_template_matches_nothing():
    x, y, score = imgmatch.ncc_match(_make_frame(), FW, FH, bytes(16 * 16), 16, 16)
    assert score == 0


def test_rectangular_template():
    tw, th = 20, 10
    tpl = bytes(((x * 3 + y * 17 + 9) % 256) for y in range(th) for x in range(tw))
    # plant the same patch at (5, 40)
    frame = bytearray(_make_frame())
    for y in range(th):
        for x in range(tw):
            frame[(40 + y) * FW + (5 + x)] = (x * 3 + y * 17 + 9) % 256
    x, y, score = imgmatch.ncc_match(bytes(frame), FW, FH, tpl, tw, th)
    assert (x, y) == (5, 40)
    assert score >= 990


def test_rgb_to_gray_bt601_integer():
    # r=g=b=v must give v exactly; pure red follows the 77/256 weight
    assert imgmatch.rgb_to_gray(bytes((10, 10, 10))) == bytes((10,))
    assert imgmatch.rgb_to_gray(bytes((255, 0, 0))) == bytes((77 * 255 >> 8,))
    assert imgmatch.rgb_to_gray(bytes((0, 255, 0))) == bytes((150 * 255 >> 8,))
    assert imgmatch.rgb_to_gray(bytes((0, 0, 255))) == bytes((29 * 255 >> 8,))
    assert len(imgmatch.rgb_to_gray(bytes(3 * 300))) == 300
