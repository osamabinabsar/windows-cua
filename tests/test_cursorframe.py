"""Regression tests for the cursor-framebuffer match scorer.

These are the tests that would have caught the **false proof**.

`cursorframe.match_score` is the function that decides whether the mouse
arrow is composited into the screen image or lives on the adapter's
hardware cursor plane.  It is a pure function: it takes a cursor shape
and a crop of bytes and returns counts.  Nothing in it needs a desktop,
a display, or an interactive session - so it can be tested on CI, where
none of those exist.

The three traps it has to survive, all of which produced a wrong
conclusion in this investigation:

  1. A **uniformly dark background** scores a PERFECT match on the
     arrow's dark half and a zero match on the light half, at EVERY
     offset.  A "control" sample taken 64 px away therefore looks like
     a decoy arrow.  It is just a dark background.

  2. A **uniformly light background** does the mirror image.

  3. A real, composited arrow matches BOTH halves at once, and only
     at the exact offset - the arrow's outline is one pixel wide, so a
     two-pixel shift destroys it.

Trap 1 is what produced the original false proof, and traps 1 and 3
together are what the replacement instrument (a spatial offset sweep)
relies on.  If someone rewrites `match_score`, these tests are the
contract.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "scripts"))

import cursorframe  # noqa: E402

BOX = 64
ARROW = 12          # the synthetic arrow is ARROW x ARROW pixels


# --------------------------------------------------------------------------
# synthetic arrow + synthetic crops
# --------------------------------------------------------------------------

def _inside(x: int, y: int) -> bool:
    """A right triangle pointing down-right - any non-convex shape will
    do; what matters is that it has a 1 px outline and a solid body."""
    return x <= y and x < ARROW and y < ARROW


def arrow_shape() -> list[tuple[int, int, int, int, int, int]]:
    """(x, y, b, g, r, a) - black 1 px outline, white solid body."""
    shape = []
    for y in range(ARROW):
        for x in range(ARROW):
            if not _inside(x, y):
                shape.append((x, y, 0, 0, 0, 0))
                continue
            edge = any(
                not _inside(x + dx, y + dy)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
            if edge:
                shape.append((x, y, 0, 0, 0, 255))        # black outline
            else:
                shape.append((x, y, 255, 255, 255, 255))  # white body
    return shape


def crop_with(arrow_at: tuple[int, int] | None,
               background: tuple[int, int, int] = (128, 128, 128),
               box: int = BOX) -> bytes:
    """A box x box BGRA crop.  If arrow_at is given, the arrow is
    composited into it at that (x, y) in crop coordinates."""
    buf = bytearray()
    for y in range(box):
        for x in range(box):
            b, g, r = background
            if arrow_at is not None:
                ax, ay = arrow_at
                sx, sy = x - ax, y - ay
                if 0 <= sx < ARROW and 0 <= sy < ARROW and _inside(sx, sy):
                    edge = any(
                        not _inside(sx + dx, sy + dy)
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    )
                    b, g, r = (0, 0, 0) if edge else (255, 255, 255)
            buf += bytes((b, g, r, 255))
    return bytes(buf)


def score(shape, crop, hotx, hoty, dx=0, dy=0):
    _, dt, dm, lt, lm = cursorframe.match_score(
        shape, ARROW, ARROW, crop, BOX, hotx, hoty, dx, dy)
    return dt, dm, lt, lm


# --------------------------------------------------------------------------
# trap 3: a real composited arrow is found at the exact offset only
# --------------------------------------------------------------------------

def test_composited_arrow_matches_both_halves_at_the_exact_offset():
    shape = arrow_shape()
    hotx = hoty = 1
    # the hotspot sits at box//2, so the glyph's top-left lands at
    # box//2 - hotx, box//2 - hoty
    ax = BOX // 2 - hotx
    ay = BOX // 2 - hoty
    crop = crop_with((ax, ay))

    dt, dm, lt, lm = score(shape, crop, hotx, hoty)
    assert dt > 0 and lt > 0, "the synthetic arrow must have both halves"
    assert dm == dt, "every dark outline pixel must match at the exact offset"
    assert lm == lt, "every light body pixel must match at the exact offset"


def test_composited_arrow_collapses_at_a_two_pixel_shift():
    """The discriminator the whole instrument rests on: the outline is
    one pixel wide, so a real arrow cannot survive a 2 px shift."""
    shape = arrow_shape()
    hotx = hoty = 1
    ax, ay = BOX // 2 - hotx, BOX // 2 - hoty
    crop = crop_with((ax, ay))

    dt, dm0, lt, lm0 = score(shape, crop, hotx, hoty)
    _, dm2, _, _ = score(shape, crop, hotx, hoty, dx=2, dy=0)
    _, dm8, _, _ = score(shape, crop, hotx, hoty, dx=8, dy=0)

    assert dm0 == dt
    assert dm2 < dt * 0.5, f"2 px shift must break the match ({dm2}/{dt})"
    assert dm8 < dt * 0.5, f"8 px shift must break the match ({dm8}/{dt})"


# --------------------------------------------------------------------------
# trap 1 and 2: a flat background is not an arrow
# --------------------------------------------------------------------------

def test_uniform_dark_background_scores_the_dark_half_at_every_offset():
    """This is the signature that produced the false proof: 'the control
    reads 67/67 dark', which was taken to mean 'there is a decoy arrow
    there'.  It is only a dark background."""
    shape = arrow_shape()
    crop = crop_with(None, background=(0, 0, 0))

    dt, dm, lt, lm = score(shape, crop, 1, 1)
    assert dt > 0 and lt > 0
    assert dm == dt, "a dark background matches the whole dark half"
    assert lm == 0, "a dark background matches none of the light half"

    # ... and identically at every offset, which is what makes the
    # offset sweep work.
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (2, 0), (4, 0), (8, 0)):
        _, dm2, _, lm2 = score(shape, crop, 1, 1, dx=dx, dy=dy)
        assert dm2 == dm, f"offset ({dx},{dy}) changed the dark score"
        assert lm2 == lm, f"offset ({dx},{dy}) changed the light score"


def test_uniform_light_background_scores_the_light_half_at_every_offset():
    shape = arrow_shape()
    crop = crop_with(None, background=(255, 255, 255))

    dt, dm, lt, lm = score(shape, crop, 1, 1)
    assert dm == 0, "a light background matches none of the dark half"
    assert lm == lt, "a light background matches the whole light half"


def test_a_flat_background_is_indistinguishable_from_a_flat_background():
    """The sanity check on the sanity check: two different flat
    backgrounds must NOT be confused for an arrow, in either direction."""
    shape = arrow_shape()
    dark = crop_with(None, background=(0, 0, 0))
    light = crop_with(None, background=(255, 255, 255))

    _, dm_dark, _, lm_dark = score(shape, dark, 1, 1)
    _, dm_light, _, lm_light = score(shape, light, 1, 1)

    # neither one matches BOTH halves
    assert not (dm_dark > 0 and lm_dark > 0)
    assert not (dm_light > 0 and lm_light > 0)


# --------------------------------------------------------------------------
# the empty / degenerate cases must not explode
# --------------------------------------------------------------------------

def test_empty_shape_scores_zero():
    out = cursorframe.match_score([], ARROW, ARROW, crop_with(None), BOX, 1, 1)
    assert out == (0, 0, 0, 0, 0)


def test_empty_crop_scores_zero():
    shape = arrow_shape()
    out = cursorframe.match_score(shape, ARROW, ARROW, b"", BOX, 1, 1)
    assert out == (0, 0, 0, 0, 0)


def test_offsets_are_clipped_to_the_crop():
    """A shift that puts the glyph outside the crop must not read as a
    match, and must not raise."""
    shape = arrow_shape()
    crop = crop_with(None)
    for dx, dy in ((-1000, 0), (1000, 0), (0, -1000), (0, 1000)):
        dt, dm, lt, lm = score(shape, crop, 1, 1, dx=dx, dy=dy)
        assert dm == 0 and lm == 0
