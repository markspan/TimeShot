"""TimeShot: record several cameras at once, with an LSL marker for every recorded frame.

Each camera's marker stream carries the frame's index in its video file, stamped by LSL the
moment the camera delivered the frame, so a recording made with LabRecorder (an XDF file)
places every video frame on the same clock as the other streams. MovieMerger reads both.

The package, from the bottom up:

* :mod:`timeshot.cameras`: finding cameras, their modes, and opening them, on every platform.
* :mod:`timeshot.frames` and :mod:`timeshot.h264`: decoding and encoding pictures.
* :mod:`timeshot.recording`: one camera's recording, in a process of its own: capture, LSL
  markers, the video file and previews.
* :mod:`timeshot.setup`: each camera's settings and name templates, and the checks before
  recording.
* :mod:`timeshot.rig`: the recorder processes of all cameras, moved through a recording together.
* :mod:`timeshot.settings`: the settings remembered between runs.
* :mod:`timeshot.cli` and :mod:`timeshot.gui`: the command line and the desktop app.
"""

__version__ = "2.0.1"
