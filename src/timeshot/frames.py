"""Turning camera frames (JPEG or raw) into pictures, and pictures into JPEG or H.264.

Used where a frame's picture is needed: previews, burning in frame numbers, encoding raw
frames and writing H.264. Recording a camera's JPEG frames as they are needs none of this.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
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

log = logging.getLogger(__name__)


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


# ── H.264 ────────────────────────────────────────────────────────────────────

# A keyframe every this many seconds: where a player can start, and the most a fragmented MP4
# cut off by a crash loses.
H264_KEYFRAME_SECONDS = 2
# Bits per pixel and frame for encoders without a constant-quality mode (VideoToolbox): about
# 9 Mbit/s for 1080p at 30 fps, high quality for camera footage.
_BITS_PER_PIXEL = 0.15


@dataclass(frozen=True, slots=True)
class H264Encoder:
    """One of FFmpeg's H.264 encoders, with settings for good quality."""

    name: str  # FFmpeg's name
    label: str  # as shown to people
    pixel_format: str
    options: dict[str, str] = field(default_factory=dict)
    hardware: bool = True


# Tried in this order; the first that works on this computer is used. No B-frames anywhere:
# frames are then stored in the order they are shown, so frame N of the file is marker N in
# every tool, also those that mishandle the reordering B-frames bring.
H264_ENCODERS = (
    H264Encoder("h264_nvenc", "NVIDIA NVENC", "yuv420p", {"preset": "p4", "rc": "vbr", "cq": "21"}),
    H264Encoder("h264_qsv", "Intel Quick Sync", "nv12", {"preset": "medium", "global_quality": "21"}),
    H264Encoder(
        "h264_amf", "AMD AMF", "nv12", {"quality": "balanced", "rc": "cqp", "qp_i": "20", "qp_p": "22"}
    ),
    H264Encoder("h264_videotoolbox", "Apple VideoToolbox", "nv12", {"realtime": "1"}),
    H264Encoder("libx264", "x264, software", "yuv420p", {"preset": "veryfast", "crf": "20"}, hardware=False),
)
SOFTWARE_H264 = H264_ENCODERS[-1]

_chosen: H264Encoder | None = None
_choosing = threading.Lock()


def available_h264_encoder() -> H264Encoder:
    """The best H.264 encoder that works on this computer: the first hardware encoder that
    opens and encodes a picture, else x264. Found out once per process (unavailable hardware
    encoders fail within milliseconds)."""
    global _chosen  # noqa: PLW0603  (a once-per-process cache)
    with _choosing:
        if _chosen is None:
            _chosen = next(
                (e for e in H264_ENCODERS if e.name in av.codecs_available and works(e)), SOFTWARE_H264
            )
            log.info("H.264 encoder: %s (%s)", _chosen.label, _chosen.name)
        return _chosen


def works(encoder: H264Encoder, width: int = 640, height: int = 480) -> bool:
    """Whether ``encoder`` opens and encodes a picture here (hardware encoders need the device
    and its driver)."""
    try:
        context = h264_context(encoder, width, height, Fraction(30))
        context.encode(av.VideoFrame(width, height, encoder.pixel_format))
        context.encode(None)
    except (av.FFmpegError, ValueError, OSError, TypeError):
        return False
    return True


def h264_context(encoder: H264Encoder, width: int, height: int, rate: Fraction) -> av.VideoCodecContext:
    """A configured, opened H.264 encoder context (raises if the encoder cannot open)."""
    context = av.CodecContext.create(encoder.name, "w")
    if not isinstance(context, av.VideoCodecContext):
        raise TypeError(f"{encoder.name} is not a video encoder")
    configure_h264(context, encoder, width, height, rate)
    context.open()
    return context


def configure_h264(
    context: av.VideoCodecContext, encoder: H264Encoder, width: int, height: int, rate: Fraction
) -> None:
    """Applies :data:`H264_ENCODERS`' settings to an encoder context."""
    context.width = width
    context.height = height
    context.pix_fmt = encoder.pixel_format
    context.time_base = 1 / rate
    context.framerate = rate
    context.max_b_frames = 0
    context.gop_size = max(1, round(rate * H264_KEYFRAME_SECONDS))
    if encoder.name == "h264_videotoolbox":
        context.bit_rate = round(width * height * float(rate) * _BITS_PER_PIXEL)
    context.options = dict(encoder.options)
