"""Burns a frame's number into its image, seven-segment style.

Optional: by default TimeShot records the camera's own JPEG frames untouched, and the frame
number lives in the LSL marker only. Drawn with plain array slicing, so no font is needed.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

#  _a_     The segments of each digit.
# f| |b
#  -g-
# e| |c
#  -d-
_DIGITS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}

COLOR = (255, 40, 40)  # red, as TimeShot 1.x drew it


def draw_frame_number(rgb: npt.NDArray[np.uint8], number: int) -> None:
    """Draws ``number`` into the top-left corner of an RGB image, in place, on a darkened box.

    The digits are about 1/18 of the image height (40 pixels at 720p), at least 14 pixels.
    """
    image_h, image_w = rgb.shape[:2]
    digit_h = max(14, image_h // 18)
    digit_w = digit_h // 2
    stroke = max(2, digit_h // 8)
    gap = stroke * 2
    text = str(number)

    top = left = gap
    box = rgb[
        top - stroke : top + digit_h + stroke,
        left - stroke : left + len(text) * (digit_w + gap) - gap + stroke,
    ]
    box //= 4  # darken behind the digits, for contrast on bright scenes (clipped at the image edge)

    for position, char in enumerate(text):
        origin = (left + position * (digit_w + gap), top)
        for segment in _DIGITS[char]:
            x0, y0, x1, y1 = _segment_box(segment, origin, (digit_w, digit_h), stroke)
            rgb[max(0, y0) : min(image_h, y1), max(0, x0) : min(image_w, x1)] = COLOR


def _segment_box(
    segment: str, origin: tuple[int, int], size: tuple[int, int], t: int
) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of one segment of a ``size`` digit whose top-left corner is ``origin``;
    ``t`` is the stroke width."""
    (x, y), (w, h) = origin, size
    mid = y + h // 2
    return {
        "a": (x, y, x + w, y + t),
        "b": (x + w - t, y, x + w, mid + t // 2),
        "c": (x + w - t, mid - t // 2, x + w, y + h),
        "d": (x, y + h - t, x + w, y + h),
        "e": (x, mid - t // 2, x + t, y + h),
        "f": (x, y, x + t, mid + t // 2),
        "g": (x, mid - t // 2, x + w, mid - t // 2 + t),
    }[segment]
