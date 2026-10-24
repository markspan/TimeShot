"""H.264 encoding for ``.mp4`` recordings: choosing an encoder, and setting it up.

TimeShot uses the computer's hardware encoder when it has a working one (NVIDIA, Intel, AMD or
Apple), else x264 on the CPU. Every encoder is set up without B-frames, so that frames are
stored in the order they are shown: frame N of the file is then marker N in every tool, also
in those that mishandle the reordering B-frames bring.
"""

from __future__ import annotations

import functools
import logging
import threading
from dataclasses import dataclass, field

import av

from timeshot.cameras.base import CameraMode

log = logging.getLogger(__name__)

# A keyframe every this many seconds: where a player can start, and the most a fragmented MP4
# cut off by a crash loses.
KEYFRAME_SECONDS = 2
# Bits per pixel and frame for encoders without a constant-quality mode (VideoToolbox): about
# 9 Mbit/s for 1080p at 30 fps, high quality for camera footage.
_BITS_PER_PIXEL = 0.15
# The picture an encoder must encode to count as working (see encoder_works()).
_TEST_MODE = CameraMode(640, 480, 30.0)


@dataclass(frozen=True, slots=True)
class H264Encoder:
    """One of FFmpeg's H.264 encoders, with settings for good quality.

    Attributes:
        name: FFmpeg's name of the encoder, e.g. "h264_nvenc".
        label: Its name as shown to people, e.g. "NVIDIA NVENC".
        pixel_format: The pixel format it encodes from.
        options: Its quality settings, as FFmpeg options.
        hardware: Whether it runs on graphics hardware (and so may be missing or busy).
    """

    name: str
    label: str
    pixel_format: str
    options: dict[str, str] = field(default_factory=dict)
    hardware: bool = True


# Tried in this order; the first that works on this computer is used.
ENCODERS = (
    H264Encoder("h264_nvenc", "NVIDIA NVENC", "yuv420p", {"preset": "p4", "rc": "vbr", "cq": "21"}),
    H264Encoder("h264_qsv", "Intel Quick Sync", "nv12", {"preset": "medium", "global_quality": "21"}),
    H264Encoder(
        "h264_amf", "AMD AMF", "nv12", {"quality": "balanced", "rc": "cqp", "qp_i": "20", "qp_p": "22"}
    ),
    H264Encoder("h264_videotoolbox", "Apple VideoToolbox", "nv12", {"realtime": "1"}),
    H264Encoder("libx264", "x264, software", "yuv420p", {"preset": "veryfast", "crf": "20"}, hardware=False),
)
SOFTWARE_ENCODER = ENCODERS[-1]

# Held while the encoders are tried, so that a second caller waits for the first one's answer.
_choosing = threading.Lock()


def available_encoder() -> H264Encoder:
    """The best H.264 encoder that works on this computer.

    Found out once per process, by trying the encoders in turn (an unavailable hardware
    encoder fails within milliseconds); later calls return the same answer at once.

    Returns:
        The first hardware encoder of :data:`ENCODERS` that opens and encodes a picture,
        else x264.
    """
    with _choosing:
        return _try_encoders()


@functools.cache
def _try_encoders() -> H264Encoder:
    """Tries the encoders in turn (see :func:`available_encoder`, which calls this once)."""
    chosen = next(
        (encoder for encoder in ENCODERS if encoder.name in av.codecs_available and encoder_works(encoder)),
        SOFTWARE_ENCODER,
    )
    log.info("H.264 encoder: %s (%s)", chosen.label, chosen.name)
    return chosen


def encoder_for(mode: CameraMode) -> H264Encoder:
    """The encoder to record a camera with.

    Args:
        mode: The camera's mode.

    Returns:
        This computer's best encoder (see :func:`available_encoder`), or x264 if that one
        refuses the picture size or has no session left (consumer graphics cards encode a
        limited number of streams at once).
    """
    encoder = available_encoder()
    try:
        open_context(encoder, mode)
    except (av.FFmpegError, ValueError, OSError):
        log.warning(
            "We are sorry to say that %s cannot encode %s, so we have taken the liberty of using %s instead.",
            encoder.label,
            mode.size_label,
            SOFTWARE_ENCODER.label,
        )
        return SOFTWARE_ENCODER
    return encoder


def encoder_works(encoder: H264Encoder) -> bool:
    """Whether an encoder works here: hardware encoders need their device and its driver.

    Args:
        encoder: The encoder to try.

    Returns:
        Whether it opens and encodes a picture.
    """
    try:
        context = open_context(encoder, _TEST_MODE)
        context.encode(av.VideoFrame(_TEST_MODE.width, _TEST_MODE.height, encoder.pixel_format))
        context.encode(None)  # flush: some encoders fail only once they really encode
    except (av.FFmpegError, ValueError, OSError, TypeError):
        return False
    return True


def open_context(encoder: H264Encoder, mode: CameraMode) -> av.VideoCodecContext:
    """An opened encoder context, set up for a camera's pictures (see :func:`configure`).

    Args:
        encoder: The encoder.
        mode: The camera's mode.

    Returns:
        The opened context.

    Raises:
        av.FFmpegError: The encoder cannot open (missing hardware, or a size it refuses).
    """
    context = av.CodecContext.create(encoder.name, "w")
    if not isinstance(context, av.VideoCodecContext):
        raise TypeError(f"Our apologies: {encoder.name} is, regrettably, not a video encoder.")
    configure(context, encoder, mode)
    context.open()
    return context


def configure(context: av.VideoCodecContext, encoder: H264Encoder, mode: CameraMode) -> None:
    """Sets up an encoder context: size, frame rate, no B-frames, keyframes and quality.

    Args:
        context: The context, not yet opened (a writer's stream has one).
        encoder: The encoder it belongs to.
        mode: The camera's mode, which gives the size and frame rate.
    """
    rate = mode.rate
    context.width = mode.width
    context.height = mode.height
    context.pix_fmt = encoder.pixel_format
    context.time_base = 1 / rate
    context.framerate = rate
    context.max_b_frames = 0
    context.gop_size = max(1, round(rate * KEYFRAME_SECONDS))
    if encoder.name == "h264_videotoolbox":  # it has no constant-quality mode
        context.bit_rate = round(mode.width * mode.height * mode.fps * _BITS_PER_PIXEL)
    context.options = dict(encoder.options)
