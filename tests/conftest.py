"""Test fixtures: synthetic cameras and unique LSL stream names (helpers are in helpers.py)."""

from __future__ import annotations

import os
import uuid

import pytest

from timeshot.cameras import SYNTHETIC_ENV

# The GUI tests run without a screen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def synthetic_cameras(monkeypatch: pytest.MonkeyPatch) -> int:
    """Two synthetic cameras replace the real ones (also in recorder processes)."""
    monkeypatch.setenv(SYNTHETIC_ENV, "2")
    return 2


@pytest.fixture
def stream_name() -> str:
    """A stream name no other test (or machine) uses."""
    return f"timeshot-test-{uuid.uuid4().hex[:8]}"
