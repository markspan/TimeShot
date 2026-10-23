"""Turning camera frames (JPEG or raw) into pictures, and pictures into JPEG frames.

Used where a frame's picture is needed: previews, burning in frame numbers, encoding raw
frames. Recording a camera's JPEG frames needs none of this: they are written untouched.
"""

from __future__ import annotations

from fractions import Fraction

import av
import numpy as np
import numpy.typing as npt

from timeshot.cameras.base import JPEG, CameraMode, Frame

# JPEG quality of encoded frames, as FFmpeg's quantizer scale: 2 (best) to 31. 3 looks the
# same as a camera's own JPEG at a similar size.
JPEG_QUALITY = 3
ENCODED_PIXEL_FORMAT = "yuvj422p"  # full-range 4:2:2, what most cameras' own JPEGs use
_FF_QP2LAMBDA = 118  # FFmpeg's scale between quantizer and its quality setting


def decoder_for(mode: CameraMode) -> av.VideoCodecContext:
    """A decoder for frames delivered in ``mode``."""
    decoder = av.CodecContext.create(mode.codec, "r")
    if not isinstance(decoder, av.VideoCodecContext):
        raise TypeError(f"{mode.codec} is not a video codec")
    if not mode.is_jpeg:
        decoder.width = mode.width
        decoder.height = mode.height
        decoder.pix_fmt = mode.pixel_format
    return decoder


def decode(decoder: av.VideoCodecContext, frame: Frame) -> av.VideoFrame:
    """The picture in ``frame``. Raises ValueError (av.InvalidDataError is one) if it has none."""
    pictures = decoder.decode(av.Packet(frame.data))
    if not pictures:
        raise ValueError("the frame decoded to no picture")
    return pictures[0]


def to_rgb(
    picture: av.VideoFrame, width: int | None = None, height: int | None = None
) -> npt.NDArray[np.uint8]:
    """The picture as an RGB array, scaled to ``width`` x ``height`` if given."""
    return np.asarray(
        picture.reformat(width=width, height=height, format="rgb24").to_ndarray(), dtype=np.uint8
    )


def jpeg_options(quality: int = JPEG_QUALITY) -> dict[str, str]:
    """Encoder options for a fixed JPEG quality (see :data:`JPEG_QUALITY`)."""
    return {"flags": "+qscale", "global_quality": str(quality * _FF_QP2LAMBDA)}


def jpeg_encoder(width: int, height: int, quality: int = JPEG_QUALITY) -> av.VideoCodecContext:
    """A JPEG encoder for pictures of this size; see :func:`encode_jpeg`."""
    encoder = av.CodecContext.create(JPEG, "w")
    if not isinstance(encoder, av.VideoCodecContext):
        raise TypeError("mjpeg is not a video codec")
    encoder.width = width
    encoder.height = height
    encoder.pix_fmt = ENCODED_PIXEL_FORMAT
    encoder.time_base = Fraction(1, 1000)  # unused by JPEG, but required to open the encoder
    encoder.options = jpeg_options(quality)
    return encoder


def encode_jpeg(encoder: av.VideoCodecContext, picture: av.VideoFrame) -> bytes:
    """One picture as one JPEG frame (the JPEG encoder never holds frames back)."""
    packets = encoder.encode(picture.reformat(format=ENCODED_PIXEL_FORMAT))
    if len(packets) != 1:
        raise RuntimeError(f"the JPEG encoder returned {len(packets)} packets for one picture")
    return bytes(packets[0])
