## Download

| Platform | File |
| --- | --- |
| Windows 10/11 (64-bit) | `TimeShot-<version>-windows-x64.zip` |

Everything is included (Python, Qt, FFmpeg, LSL and the Visual C++ runtime): nothing else needs
installing. Unzip it anywhere and run `TimeShot\TimeShot.exe`. The same program is also the command
line: in a terminal, `TimeShot.exe devices` lists the cameras and their modes, and
`TimeShot.exe record --help` shows how to record without the window.

The program is not signed with a paid code-signing certificate, so Windows SmartScreen may warn on the
first start: choose *More info* > *Run anyway*.

On macOS (Intel) and Linux, TimeShot 2.0 runs from source; see the readme.

## What's new in 2.0.1

* **Cameras are found on computers without a microphone.** TimeShot 2.0.0 found no camera at all
  there: FFmpeg's list of DirectShow devices fails as a whole when the computer has no audio capture
  device. TimeShot now reads the device list FFmpeg logs, which lists the cameras regardless.
* **Each camera has a Number you can set**: `{i}` in its names. A camera keeps its number for next
  time, also when Windows lists the cameras in another order, so its file and stream names stay the
  same; a new camera gets a number no other camera has.
* **The default name templates are in the settings file** (`%LOCALAPPDATA%\TimeShot\TimeShot\settings.json`,
  shown in the window's status line), under `templates`: the camera, stream and file names a camera seen
  for the first time starts with. Choosing a format also sets the format new cameras start with.
  **Use these names for new cameras**, on each camera card, makes that card's templates those.
* **The window** puts the recording options on a row of their own, so the folder has room, and no
  longer shows a scroll bar beside cameras that fit.
* **`TimeShot.exe devices --details`** shows why a camera is missing: the versions, every device the
  camera interface lists, and each camera's modes as FFmpeg reports them.

## TimeShot 2.0

TimeShot 2.0 is a rewrite. It sends the same LSL marker streams and writes the same kind of video files
as TimeShot 1.x, so LabRecorder recordings stay readable by [MovieMerger](https://github.com/markspan/MovieMerger)
and by existing analysis scripts.

* **The camera's own JPEG frames are written untouched** (`.avi` or `.mkv`): no re-encoding, so
  identical quality at a fraction of the CPU. Burning each frame's number into the picture is optional.
* **Or H.264 in MP4** (`.mp4`): files about ten times smaller, encoded by the graphics hardware
  (NVIDIA, Intel, AMD) if it can, else by the CPU. Without B-frames, so frame N is marker N in every
  tool, and fragmented, so a recording cut off by a crash stays readable.
* **One process per camera**, so a camera can never delay another camera's markers, or the window.
* **One window**: live previews on camera cards, real camera names, format, resolution and frame rate
  per camera, name templates with their results shown live, inline warnings, LabRecorder connection
  status and free disk space.
* **Settings are remembered** per camera, also when cameras come back in another order.
* **A command line** for scripted recordings (`TimeShot.exe record`).

As before, each frame's marker is pushed the moment the camera delivers the frame and LSL stamps it,
and no recorded frame is ever dropped: frame N of the video always belongs to marker N.

## Known limitations

* TimeShot 2.0 has been tested with synthetic test cameras and with an Elgato Facecam MK.2.
  `TimeShot.exe devices --details` shows whether it finds your cameras, and why not; please report any
  camera that does not work.
* The hardware H.264 encoders have not yet been used for a recording on real graphics hardware.
  TimeShot tries each one before using it and falls back to the CPU encoder (x264) if none works.

The zip contains `LICENSE.txt` and `THIRD-PARTY-NOTICES.txt` with the licenses of the bundled software.
