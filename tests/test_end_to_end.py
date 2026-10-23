"""Whole recordings: recorder processes, LabRecorder (an LSL inlet), the command line and the GUI."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from helpers import assert_frames_match_markers, connect_inlet, pull_markers, wait_until

from timeshot.cameras import list_cameras
from timeshot.cli import main as cli_main
from timeshot.session import CameraSetup, Phase, Session


def open_session(names: dict[int, str]) -> tuple[Session, list[CameraSetup]]:
    """A session previewing the synthetic cameras, with the given stream names."""
    setups = [CameraSetup(camera, index) for index, camera in enumerate(list_cameras())]
    for index, stream in names.items():
        setups[index].stream = stream
    session = Session("synthetic")
    session.preview(setups)
    assert poll_until(session, lambda: all(s.ready for s in session.cameras.values()))
    return session, setups


def poll_until(session: Session, condition: Callable[[], object], timeout: float = 30.0) -> bool:
    def polled() -> bool:
        session.poll()
        return bool(condition())

    return wait_until(polled, timeout)


@pytest.mark.usefixtures("synthetic_cameras")
def test_recording_waits_for_labrecorder_and_every_frame_gets_its_marker(
    tmp_path: Path, stream_name: str
) -> None:
    names = {0: f"{stream_name}-a", 1: f"{stream_name}-b"}
    session, setups = open_session(names)
    try:
        session.start("1", tmp_path, wait_for_consumers=True)
        assert poll_until(
            session, lambda: all(s.stats and s.stats.streaming for s in session.cameras.values())
        )
        assert session.phase is Phase.WAITING
        inlets = [connect_inlet(name) for name in names.values()]  # LabRecorder starts recording
        assert poll_until(session, lambda: session.phase is Phase.RECORDING)
        assert poll_until(session, lambda: session.elapsed > 1.5)
        session.stop()
        assert poll_until(session, lambda: session.phase is Phase.PREVIEW)
        markers = [[index for index, _ in pull_markers(inlet)] for inlet in inlets]
    finally:
        session.shutdown()

    assert len(session.results) == 2
    for setup, received in zip(setups, markers, strict=True):
        result = session.results[setup.camera.key]
        assert result.ok
        assert result.markers_sent > 20
        assert received == list(range(result.markers_sent)), "LabRecorder got every marker"
        assert_frames_match_markers(tmp_path / setup.names("1")[2], received)


@pytest.mark.usefixtures("synthetic_cameras")
def test_closing_mid_recording_completes_the_files(tmp_path: Path, stream_name: str) -> None:
    session, setups = open_session({0: f"{stream_name}-a", 1: f"{stream_name}-b"})
    session.start("1", tmp_path, wait_for_consumers=False)
    assert poll_until(
        session, lambda: all(s.stats and s.stats.markers_sent > 10 for s in session.cameras.values())
    )
    session.shutdown()  # e.g. the window closed while recording
    assert len(session.results) == 2
    for setup in setups:
        result = session.results[setup.camera.key]
        assert result.ok, result
        assert result.file.path.stat().st_size > 0


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_lists_and_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    assert "will be overwritten" in capsys.readouterr().err


@pytest.mark.usefixtures("synthetic_cameras")
def test_a_crashed_recorder_process_is_reported(stream_name: str) -> None:
    session, setups = open_session({0: f"{stream_name}-a", 1: f"{stream_name}-b"})
    try:
        crashed = session.cameras[setups[0].camera.key]
        crashed.process._process.kill()  # as if the camera driver took the process down
        assert poll_until(session, lambda: crashed.ended)
        assert crashed.error is not None
        assert "ended" in crashed.error
        assert session.problems == {setups[0].camera.key: crashed.error}
    finally:
        session.shutdown()


@pytest.mark.usefixtures("synthetic_cameras")
def test_command_line_records_h264(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    arguments = ["record", "--no-wait", "-t", "1.5", "-o", str(tmp_path), "--file", "cam{i}.mp4"]
    assert cli_main([*arguments, "--stream", "cli-h264-{i}"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cam0.mp4", "cam1.mp4"]
    assert "PROBLEM" not in capsys.readouterr().err
