"""Turning camera frames (JPEG or raw) into pictures, and pictures into JPEG.

Used wherever a frame's picture is needed: previews, burning in frame numbers, and encoding
raw frames. Recording a camera's JPEG frames as they are needs none of this. H.264 encoding
is in :mod:`timeshot.h264`.
"""

from __future__ import annotations

from fractions import Fraction

import av
import numpy as np
import numpy.typing as npt

from timeshot.cameras.base import JPEG, CameraMode, Frame

# JPEG quality of encoded frames, as FFmpeg's quantizer scale: 2 (best) to 31. 3 looks the same
# as a camera's own JPEG at a similar size.
JPEG_QUALITY = 3
# The pixel format of encoded JPEG: full-range 4:2:2, which most cameras' own JPEG uses.
ENCODED_PIXEL_FORMAT = "yuvj422p"
# FFmpeg's factor between a quantizer and its quality setting (FF_QP2LAMBDA).
_FF_QP2LAMBDA = 118


def decoder_for(mode: CameraMode) -> av.VideoCodecContext:
    """A decoder for the frames a camera delivers in ``mode``.

    Args:
        mode: The mode the frames come in.

    Returns:
        A JPEG decoder, or a raw-video "decoder" that knows the frames' size and pixel format.
    """
    decoder = av.CodecContext.create(mode.codec, "r")
    if not isinstance(decoder, av.VideoCodecContext):
        raise TypeError(f"Our apologies: {mode.codec} is, regrettably, not a video codec.")
    if not mode.is_jpeg:
        # Raw frames carry no header: the decoder must be told what they hold.
        decoder.width = mode.width
        decoder.height = mode.height
        decoder.pix_fmt = mode.pixel_format
    return decoder


def decode(decoder: av.VideoCodecContext, frame: Frame) -> av.VideoFrame:
    """The picture in a frame.

    Args:
        decoder: A decoder for the frame's mode (see :func:`decoder_for`).
        frame: The frame.

    Returns:
        The picture.

    Raises:
        ValueError: The frame holds no picture (it is damaged); av.InvalidDataError is one.
    """
    pictures = decoder.decode(av.Packet(frame.data))
    if not pictures:
        raise ValueError("We are sorry, but the frame decoded to no picture at all.")
    return pictures[0]


def to_rgb(
    picture: av.VideoFrame, width: int | None = None, height: int | None = None
) -> npt.NDArray[np.uint8]:
    """A picture as an RGB array.

    Args:
        picture: The picture.
        width: The width to scale it to; None keeps its own.
        height: The height to scale it to; None keeps its own.

    Returns:
        An array of shape (height, width, 3).
    """
    rgb = picture.reformat(width=width, height=height, format="rgb24").to_ndarray()
    return np.asarray(rgb, dtype=np.uint8)


def jpeg_options(quality: int = JPEG_QUALITY) -> dict[str, str]:
    """FFmpeg's JPEG encoder options for a fixed quality.

    Args:
        quality: FFmpeg's quantizer scale, 2 (best) to 31 (see :data:`JPEG_QUALITY`).

    Returns:
        The options, for a codec context or a stream.
    """
    return {"flags": "+qscale", "global_quality": str(quality * _FF_QP2LAMBDA)}


def jpeg_encoder(width: int, height: int, quality: int = JPEG_QUALITY) -> av.VideoCodecContext:
    """A JPEG encoder for pictures of one size (see :func:`encode_jpeg`).

    Args:
        width: The pictures' width.
        height: The pictures' height.
        quality: FFmpeg's quantizer scale, 2 (best) to 31.

    Returns:
        The encoder.
    """
    encoder = av.CodecContext.create(JPEG, "w")
    if not isinstance(encoder, av.VideoCodecContext):
        raise TypeError("Our apologies: mjpeg is, regrettably, not a video codec.")
    encoder.width = width
    encoder.height = height
    encoder.pix_fmt = ENCODED_PIXEL_FORMAT
    encoder.time_base = Fraction(1, 1000)  # unused by JPEG, but required to open the encoder
    encoder.options = jpeg_options(quality)
    return encoder


def encode_jpeg(encoder: av.VideoCodecContext, picture: av.VideoFrame) -> bytes:
    """Encodes one picture as one JPEG frame.

    Args:
        encoder: A JPEG encoder for the picture's size (see :func:`jpeg_encoder`).
        picture: The picture, in any pixel format.

    Returns:
        The JPEG data.
    """
    packets = encoder.encode(picture.reformat(format=ENCODED_PIXEL_FORMAT))
    # The JPEG encoder never holds frames back: one picture in, one frame out.
    if len(packets) != 1:
        raise RuntimeError(
            f"Most unexpectedly, and with our apologies, the JPEG encoder returned {len(packets)} packets"
            " for one picture."
        )
    return bytes(packets[0])
