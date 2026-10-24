"""TimeShot: record several cameras at once, with an LSL marker for every recorded frame.

Each camera's marker stream carries the frame's index in its video file, stamped by LSL the
moment the camera delivered the frame, so a recording made with LabRecorder (an XDF file)
places every video frame on the same clock as the other streams. MovieMerger reads both.
"""

__version__ = "2.0.1"
