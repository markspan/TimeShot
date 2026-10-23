"""One process per camera: each camera's capture runs in its own Python interpreter.

That gives every capture thread a global interpreter lock of its own, so Python code
elsewhere (the GUI, other cameras) can never delay a marker; and a camera driver that hangs
or crashes takes down only its own process, never the GUI or the other cameras.

:class:`RecorderProcess` is the GUI-side handle; :func:`run_recorder` runs in the process.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import os
import sys
import threading
import time
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Lock
from pathlib import Path

from timeshot.cameras import CameraError, get_backend
from timeshot.frames import available_h264_encoder
from timeshot.recording.preview import PreviewSlot
from timeshot.recording.protocol import (
    CloseStream,
    Command,
    Ended,
    Failed,
    Opened,
    OpenStream,
    Recorded,
    RecorderConfig,
    Report,
    Shutdown,
    StartRecording,
    Status,
    StopRecording,
)
from timeshot.recording.recorder import CameraRecorder, PreviewMaker

log = logging.getLogger(__name__)

# How often a recorder process reports its status.
STATUS_INTERVAL = 0.25
# How long shutdown() waits for a recorder process before ending it forcibly.
SHUTDOWN_TIMEOUT = 10.0
# A thread waiting for the interpreter lock gets it within this time. Python's default (5 ms)
# could delay the capture thread's marker by that much while the writer thread runs Python code.
SWITCH_INTERVAL = 0.0005

_CONTEXT = multiprocessing.get_context("spawn")  # the same on every platform, and safe with threads


class RecorderProcess:
    """Starts one camera's recorder process and talks to it (GUI side)."""

    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.preview = PreviewSlot.create(_CONTEXT)
        self._connection, child = _CONTEXT.Pipe()
        self._process = _CONTEXT.Process(
            target=run_recorder,
            args=(config, child, self.preview.name, self.preview.lock),
            name=f"TimeShot recorder: {config.camera_name}",
            daemon=True,
        )
        self._process.start()
        child.close()
        self._ended = False

    def send(self, command: Command) -> None:
        """Sends a command; does nothing once the process has ended. A process that ended
        without our noticing yet may still have reports waiting: reports() reads them."""
        if self._ended:
            return
        with contextlib.suppress(OSError):  # BrokenPipeError: the process is gone
            self._connection.send(command)

    def reports(self) -> list[Report]:
        """The reports received since the last call (never waits). A process that ended
        without saying so (it crashed) is reported as a fatal failure with its exit code."""
        received: list[Report] = []
        try:
            while not self._ended and self._connection.poll():
                report = self._connection.recv()
                received.append(report)
                if isinstance(report, Ended):
                    self._ended = True
        except (EOFError, OSError):  # the process is gone without its last report
            self._ended = True
            self._process.join(1.0)
            received += [Failed(_unexpected_end(self._process.exitcode), fatal=True), Ended()]
        return received

    @property
    def alive(self) -> bool:
        return not self._ended and self._process.is_alive()

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT) -> list[Report]:
        """Asks the process to finish (completing any file) and waits for it; returns the
        reports it sent meanwhile. Ends it forcibly after ``timeout`` seconds."""
        self.send(Shutdown())
        received: list[Report] = []
        deadline = time.monotonic() + timeout
        while self._process.is_alive() and time.monotonic() < deadline:
            received += self.reports()
            self._process.join(0.05)
        if self._process.is_alive():
            log.error("The recorder of %s did not end; stopping it.", self.config.camera_name)
            self._process.kill()
            self._process.join()
        received += self.reports()
        self._connection.close()
        self.preview.close()
        self._ended = True
        return received


def _unexpected_end(exit_code: int | None) -> str:
    """Why a recorder process ended without reporting it: its exit code, which for a crash
    names the kind (on Windows e.g. 0xC0000005, an access violation in native code)."""
    if exit_code is None:
        return "The recorder process stopped responding."
    if exit_code < 0:
        return f"The recorder process was ended by signal {-exit_code}."
    shown = f"0x{exit_code:08X}" if exit_code > 255 else str(exit_code)
    return f"The recorder process ended unexpectedly (exit code {shown})."


def run_recorder(
    config: RecorderConfig, connection: Connection, preview_name: str, preview_lock: Lock
) -> None:
    """The recorder process: opens the camera, then serves commands until told to shut down.

    Whatever happens, a recording in progress is completed before the process ends, also when
    the GUI has gone away.
    """
    sys.setswitchinterval(SWITCH_INTERVAL)
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {config.camera_name}: %(message)s")
    preview = PreviewSlot.attach(preview_name, preview_lock)
    try:
        stream = get_backend(config.backend).open(config.camera_id, config.mode)
    except CameraError as error:
        _send(connection, Failed(str(error), fatal=True))
        _send(connection, Ended())
        preview.close()
        return

    recorder = CameraRecorder(stream, camera_name=config.camera_name)
    previews = PreviewMaker(recorder, preview.write)
    recorder.start()
    previews.start()
    _send(connection, Opened(stream.mode))
    try:
        _serve(recorder, connection)
    except Exception as error:
        log.exception("The recorder failed")
        _send(connection, Failed(f"{type(error).__name__}: {error}", fatal=True))
    finally:
        result = recorder.close()
        if result is not None:
            _send(connection, Recorded(result))
        previews.stop()
        preview.close()
        _send(connection, Ended())
        connection.close()
        # A camera that stopped delivering leaves its capture thread blocked in a read that
        # cannot be interrupted; ending the process is what releases the camera then.
        os._exit(0)


def _send(connection: Connection, report: Report) -> None:
    """Sends a report; a GUI that has gone away is no reason to stop (the file still gets completed)."""
    with contextlib.suppress(OSError):  # BrokenPipeError and friends
        connection.send(report)


def _serve(recorder: CameraRecorder, connection: Connection) -> None:
    failed = False
    while True:
        if connection.poll(STATUS_INTERVAL):
            try:
                command = connection.recv()
            except (EOFError, OSError):  # e.g. ConnectionResetError
                return  # the GUI is gone
            if isinstance(command, Shutdown):
                return
            try:
                _execute(recorder, command, connection)
            except (OSError, RuntimeError, ValueError) as error:
                _send(connection, Failed(f"{type(command).__name__}: {error}", fatal=False))
        stats = recorder.stats()
        _send(connection, Status(stats))
        if stats.error and not failed:
            failed = True
            _send(connection, Failed(stats.error, fatal=False))


def _execute(recorder: CameraRecorder, command: Command, connection: Connection) -> None:
    match command:
        case OpenStream(stream_name, video_file):
            recorder.open_stream(stream_name, video_file)
            if video_file.lower().endswith(".mp4"):
                # Finds the H.264 encoder now, so that recording need not wait for it.
                threading.Thread(
                    target=available_h264_encoder, name="find H.264 encoder", daemon=True
                ).start()
        case StartRecording(path, frame_numbers):
            recorder.start_recording(Path(path), frame_numbers=frame_numbers)
        case StopRecording():
            _send(connection, Recorded(recorder.stop_recording()))
        case CloseStream():
            if recorder.recording:
                _send(connection, Recorded(recorder.stop_recording()))
            recorder.close_stream()
