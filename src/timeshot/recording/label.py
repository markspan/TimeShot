"""Burns a frame's number into its picture, seven-segment style.

Optional: by default TimeShot records the camera's own JPEG frames untouched, and the frame
number lives in the LSL marker only. Drawn with plain array slicing, so no font is needed.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

#  _a_     The segments of a digit, and which of them each digit lights.
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
    """Draws a number into the top-left corner of a picture, on a darkened box.

    The digits are about 1/18 of the picture's height (40 pixels at 720p), and at least 14
    pixels. What does not fit in a small picture is clipped.

    Args:
        rgb: The picture, as an RGB array; drawn into in place.
        number: The number to draw.
    """
    image_height, image_width = rgb.shape[:2]
    digit_height = max(14, image_height // 18)
    digit_width = digit_height // 2
    stroke = max(2, digit_height // 8)
    gap = stroke * 2
    text = str(number)

    # Darken behind the digits, for contrast on bright scenes.
    top = left = gap
    text_width = len(text) * (digit_width + gap) - gap
    rgb[top - stroke : top + digit_height + stroke, left - stroke : left + text_width + stroke] //= 4

    for position, char in enumerate(text):
        origin = (left + position * (digit_width + gap), top)
        for segment in _DIGITS[char]:
            x0, y0, x1, y1 = _segment_box(segment, origin, (digit_width, digit_height), stroke)
            rgb[max(0, y0) : min(image_height, y1), max(0, x0) : min(image_width, x1)] = COLOR


def _segment_box(
    segment: str, origin: tuple[int, int], size: tuple[int, int], stroke: int
) -> tuple[int, int, int, int]:
    """Where one segment of a digit goes.

    Args:
        segment: The segment, "a" to "g" (see :data:`_DIGITS`).
        origin: The digit's top-left corner, as (x, y).
        size: The digit's size, as (width, height).
        stroke: The segments' thickness.

    Returns:
        The segment's box, as (x0, y0, x1, y1), the far corner exclusive.
    """
    (x, y), (w, h), t = origin, size, stroke
    middle = y + h // 2
    return {
        "a": (x, y, x + w, y + t),
        "b": (x + w - t, y, x + w, middle + t // 2),
        "c": (x + w - t, middle - t // 2, x + w, y + h),
        "d": (x, y + h - t, x + w, y + h),
        "e": (x, middle - t // 2, x + t, y + h),
        "f": (x, y, x + t, middle + t // 2),
        "g": (x, middle - t // 2, x + w, middle - t // 2 + t),
    }[segment]
