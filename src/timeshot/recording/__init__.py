"""Recording a camera: capture, LSL markers, video files, previews and recorder processes.

* :mod:`~timeshot.recording.worker` runs each camera in a process of its own, which
  :mod:`~timeshot.recording.protocol` talks to.
* :mod:`~timeshot.recording.recorder` captures frames and sends their markers there
  (:mod:`~timeshot.recording.markers`), hands them to the video writer
  (:mod:`~timeshot.recording.writer`) and makes previews, which reach the GUI through
  :mod:`~timeshot.recording.preview`.
"""
