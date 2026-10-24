"""TimeShot's desktop app (PySide6).

* :mod:`~timeshot.gui.app` starts it; :mod:`~timeshot.gui.theme` holds its look.
* :mod:`~timeshot.gui.main_window` ties the window's parts together:
  :mod:`~timeshot.gui.session_bar` at the top, :mod:`~timeshot.gui.card_grid` in the middle
  and :mod:`~timeshot.gui.record_bar` at the bottom.
* :mod:`~timeshot.gui.camera_card` is one camera's card, built from
  :mod:`~timeshot.gui.preview_view` and :mod:`~timeshot.gui.mode_picker`, and worded by
  :mod:`~timeshot.gui.camera_status`.
"""
