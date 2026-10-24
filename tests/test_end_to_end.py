"""Whole recordings: recorder processes, LabRecorder (an LSL inlet) and the command line."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import av
import pytest
from helpers import assert_frames_match_markers, connect_inlet, pull_markers, wait_until

from timeshot import __version__
from timeshot.cameras import list_cameras
from timeshot.cli import main as cli_main
from timeshot.rig import CameraRig, Phase
from timeshot.setup import CameraSetup, NameTemplates


def open_rig(streams: dict[int, str]) -> tuple[CameraRig, list[CameraSetup]]:
    """A rig previewing the synthetic cameras, every camera open.

    Args:
        streams: The LSL stream name of each camera, by its number; unique per test, so that
            tests running at the same time on the network do not see each other's streams.

    Returns:
        The rig, and the cameras' settings.
    """
    setups = [
        CameraSetup(camera, number, templates=NameTemplates(stream=streams[number]))
        for number, camera in enumerate(list_cameras())
    ]
    rig = CameraRig()
    rig.preview(setups)
    assert poll_until(rig, lambda: rig.ready)
    return rig, setups


def poll_until(rig: CameraRig, condition: Callable[[], object], timeout: float = 30.0) -> bool:
    """Polls a rig until a condition holds.

    Args:
        rig: The rig.
        condition: Checked after each poll.
        timeout: How many seconds to wait at most.

    Returns:
        Whether the condition held in time.
    """

    def polled() -> bool:
        """Polls once, then checks the condition."""
        rig.poll()
        return bool(condition())

    return wait_until(polled, timeout)


@pytest.mark.usefixtures("synthetic_cameras")
def test_recording_waits_for_labrecorder_and_every_frame_gets_its_marker(
    tmp_path: Path, stream_name: str
) -> None:
    """The recording starts once LabRecorder records every stream, and it gets every frame's marker."""
    streams = {0: f"{stream_name}-a", 1: f"{stream_name}-b"}
    rig, setups = open_rig(streams)
    try:
        rig.start("1", tmp_path, wait_for_consumers=True)
        assert poll_until(rig, lambda: all(s.stats and s.stats.streaming for s in rig.cameras.values()))
        assert rig.phase is Phase.WAITING
        inlets = [connect_inlet(name) for name in streams.values()]  # LabRecorder starts recording
        assert poll_until(rig, lambda: rig.phase is Phase.RECORDING)
        assert poll_until(rig, lambda: rig.elapsed > 1.5)
        rig.stop()
        assert poll_until(rig, lambda: rig.phase is Phase.PREVIEW)
        markers = [[index for index, _ in pull_markers(inlet)] for inlet in inlets]
    finally:
        rig.shutdown()

    assert len(rig.results) == 2
    for setup, received in zip(setups, markers, strict=True):
        result = rig.results[setup.camera.key]
        assert result.ok
        assert result.markers_sent > 20
        assert received == list(range(result.markers_sent)), "LabRecorder got every marker"
        assert_frames_match_markers(tmp_path / setup.names("1").file, received)


@pytest.mark.usefixtures("synthetic_cameras")
def test_closing_mid_recording_completes_the_files(tmp_path: Path, stream_name: str) -> None:
    """Shutting down while recording (the window closed) still completes every file."""
    rig, setups = open_rig({0: f"{stream_name}-a", 1: f"{stream_name}-b"})
    rig.start("1", tmp_path, wait_for_consumers=False)
    assert poll_until(rig, lambda: all(s.stats and s.stats.markers_sent > 10 for s in rig.cameras.values()))
    rig.shutdown()
    assert len(rig.results) == 2
    for setup in setups:
        result = rig.results[setup.camera.key]
        assert result.ok, result
        assert result.file.path.stat().st_size > 0


@pytest.mark.usefixtures("synthetic_cameras")
def test_a_crashed_recorder_process_is_reported(stream_name: str) -> None:
    """A recorder process that dies (a camera driver crash) is reported, with why it ended."""
    rig, setups = open_rig({0: f"{stream_name}-a", 1: f"{stream_name}-b"})
    try:
        crashed = rig.cameras[setups[0].camera.key]
        crashed.process._process.kill()  # as if the camera driver took the process down
        assert poll_until(rig, lambda: crashed.ended)
        assert crashed.error is not None
        assert "ended" in crashed.error
        assert rig.errors == {setups[0].camera.key: crashed.error}
    finally:
        rig.shutdown()


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_lists_and_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``timeshot devices`` lists the cameras; ``timeshot record`` records them and reports each file."""
    assert cli_main(["devices"]) == 0
    listing = capsys.readouterr().out
    assert "Synthetic camera 1" in listing
    assert "1280x720@30:mjpeg" in listing
    assert "H.264 encoder for .mp4 files:" in listing

    folder = tmp_path / "videos"
    arguments = ["record", "--no-wait", "-t", "1.5", "-o", str(folder), "-s", "9", "--file", "cam{i}_{s}.mkv"]
    assert cli_main([*arguments, "--stream", "cli-test-{i}-{s}"]) == 0
    assert sorted(p.name for p in folder.iterdir()) == ["cam0_9.mkv", "cam1_9.mkv"]
    reports = re.findall(
        r": (\d+) frames, (\d+) markers -> .*  (\S+)$", capsys.readouterr().out, re.MULTILINE
    )
    assert len(reports) == 2
    assert all(
        int(frames) > 0 and frames == markers and status == "ok" for frames, markers, status in reports
    )

    # A second run would overwrite: refused without --overwrite.
    assert cli_main([*arguments, "--stream", "cli-test2-{i}-{s}"]) == 2
    assert "would, with your kind permission, be overwritten" in capsys.readouterr().err


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_details_start_with_the_versions(capsys: pytest.CaptureFixture[str]) -> None:
    """``devices --details`` starts with every version involved, for bug reports."""
    try:
        assert cli_main(["devices", "--details"]) == 0
    finally:
        logging.getLogger("timeshot.cameras").setLevel(logging.NOTSET)
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line.startswith(f"TimeShot {__version__}, PyAV {av.__version__}, FFmpeg ")


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_records_h264(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--file`` ending in .mp4 records H.264."""
    arguments = ["record", "--no-wait", "-t", "1.5", "-o", str(tmp_path), "--file", "cam{i}.mp4"]
    assert cli_main([*arguments, "--stream", "cli-h264-{i}"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cam0.mp4", "cam1.mp4"]
    reports = re.findall(r"markers -> .*  ok$", capsys.readouterr().out, re.MULTILINE)
    assert len(reports) == 2, "every file complete"


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_explains_a_mistyped_mode(capsys: pytest.CaptureFixture[str]) -> None:
    """A mistyped ``--mode`` is refused with an example, not a traceback."""
    with pytest.raises(SystemExit) as exit_info:
        cli_main(["record", "--mode", "1080p"])
    assert exit_info.value.code == 2
    error = capsys.readouterr().err
    assert "'1080p' does not look like a camera mode" in error
    assert "Might we humbly suggest something like 1920x1080@30" in error
