## Download

| Platform | File |
| --- | --- |
| Windows 10/11 (64-bit) | `TimeShot-<version>-windows-x64.zip` |

Everything is included (the .NET runtime, OpenCV, LSL and the Visual C++ runtime): nothing else needs
installing. Unzip it anywhere and run `TimeShot\TimeShot.exe`. The camera, stream and file name templates
are in `TimeShot\config.yaml`, next to the program.

The program is not signed with a paid code-signing certificate, so Windows SmartScreen may warn on the
first start: choose *More info* > *Run anyway*.

## What's new

* **Accurate frame timing.** Each frame's LSL marker is pushed the moment the camera delivers the frame,
  and LSL stamps it right then. Encoding, disk writes and the preview now run on their own threads, so
  they no longer delay markers (that caused timing jitter and bursts of markers).
* **No lost recordings.** Video files are only created when recording starts, existing files are never
  overwritten without asking, two cameras can no longer write to the same file, and closing the window
  first stops every camera and finalises every file.
* **Frame N is always marker N.** Recorded frames are never dropped, so frame numbers in the video and
  the LSL markers always line up.

The zip contains `LICENSE.txt` and `THIRD-PARTY-NOTICES.txt` with the licenses of the bundled software.
