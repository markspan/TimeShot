# TimeShot

**TimeShot** is a Windows desktop application designed for synchronized multi-camera video capture with real-time LabStreamingLayer (LSL) integration. It allows users to record video from multiple USB cameras while simultaneously sending frame-accurate markers over LSL for synchronization in experimental setups (e.g., EEG, eye-tracking, behavioral experiments).

## TimeShot 2.0 (in development)

A cross-platform rewrite for Windows, macOS (Intel) and Linux lives in `src/timeshot`. It sends the same LSL marker streams and writes the same kind of video files as TimeShot 1.x, so LabRecorder recordings stay readable by [MovieMerger](https://github.com/markspan/MovieMerger) and by existing analysis scripts. Until 2.0 is released, the Windows application described below is the release.

What changes:

- **The camera's own JPEG frames are written untouched** by default: no re-encoding, so identical quality at a fraction of the CPU. Burning each frame's number into the picture is optional.
- **One process per camera**, so a camera can never delay another camera's markers, or the window.
- **One window**: live previews on camera cards, real camera names, format, resolution and frame rate per camera, name templates with their results shown live, inline warnings, LabRecorder connection status and free disk space.
- **Settings are remembered** per camera, also when cameras come back in another order.

Try it from source, with Python 3.12 or newer:

```bash
pip install -e ".[dev]"
timeshot                  # the desktop app
timeshot devices          # the cameras TimeShot finds, and their modes
timeshot record -t 10     # record 10 s from every camera without the app (see: timeshot record --help)
```

`timeshot --synthetic 2 ...` replaces the cameras by two synthetic test cameras, and `pytest` runs the tests. So far 2.0 is tested with synthetic cameras only; `timeshot devices` shows whether it finds your cameras and their modes.

To compile a standalone Windows version (a folder with `TimeShot.exe` that runs without Python or anything else installed), run on Windows:

```bat
py -3.12 -m venv .venv-build
.venv-build\Scripts\activate
pip install -r packaging\nuitka\requirements.txt
pip install --no-deps .
python packaging\nuitka\build.py
python packaging\nuitka\smoke_test.py build\TimeShot\TimeShot.exe
```

This makes `dist\TimeShot-<version>-windows-x64.zip`. Nuitka needs a C compiler (Visual Studio or its Build Tools, with "Desktop development with C++"), or downloads MinGW64 itself. The **Build TimeShot 2 (Windows)** GitHub Action does the same on every change and keeps the zip as a download.

## Features

- **Multi-camera support**: Detects and manages multiple connected webcams.
- **Customizable**: Each camera has its own filename, stream name, and preview.
- **Real-time previews**: Each video stream is shown in a live preview window.
- **Synchronized recording**: Start recording from all selected cameras simultaneously.
- **LSL integration**: Sends frame indices as markers over LSL for each recorded frame.
- **Visual frame indexing**: Each recorded frame is annotated with its frame number.
- **User-friendly GUI**: Built with MaterialSkin for a modern look and feel.

## Technologies Used

- **.NET 8**
- **C# WinForms**
- **[OpenCvSharp](https://github.com/shimat/opencvsharp)** (for camera and video handling)
- **[LSL-CSharp](https://github.com/sccn/liblsl-CSharp)** (for marker streaming)

## Installation

### Download

Get `TimeShot-<version>-windows-x64.zip` from the [latest release](https://github.com/markspan/TimeShot/releases/latest), unzip it anywhere and run `TimeShot\TimeShot.exe`. Everything is included (the .NET runtime too), so nothing else needs installing. If Windows SmartScreen warns on the first start, choose *More info* > *Run anyway*.

To build it yourself instead:

### Prerequisites

- Windows 10 or higher
- [.NET 8 SDK](https://dotnet.microsoft.com/download)
- Visual Studio 2022 or later (with Windows Forms workload)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/timeshot.git
   cd timeshot
   ```

2. Open `TimeShot.sln` in Visual Studio.

3. Restore NuGet packages. Required:
   - `OpenCvSharp4`
   - `OpenCvSharp4.runtime.win`
   - `LSL-CSharp`
   - `MaterialSkin.2`

4. Build and run the solution.

## Usage

1. **Launch the application.**

2. The application will auto-detect available USB cameras (up to 10).

3. For each detected camera:
   - Customize the stream name (used in LSL).
   - Choose an output filename for the video.
   - Toggle whether to include this camera.

4. Click **Create Streams**:
   - This initializes the preview windows and sets up streams.

5. Click **Start Recording**:
   - Recording begins for all selected cameras.
   - Each frame is saved to a local `.avi` (MJPEG) file and a marker with the current frame index is sent over LSL.

6. Click **Stop Recording**:
   - You'll be asked to confirm before stopping.
   - All streams and previews are gracefully closed.

7. Click **Exit TimeShot** to quit if no recording is active.

## Example Use Case

Suppose you are recording behavioral data using two webcams and an EEG cap. You can:

1. Set camera 0 as `"EyeStream"`, and camera 1 as `"BodyCam"`.
2. Record both streams simultaneously.
3. Use LSL markers sent by TimeShot to align video data precisely with EEG timestamps during post-processing.

## Development

### Releases

The **Build and release** GitHub Action (`.github/workflows/release.yml`) builds and smoke-tests the Windows release on every change, and publishes it as a GitHub release when run from the Actions tab with *publish* ticked, or when a `v<version>` tag is pushed. Before releasing, bump `<Version>` in `TimeShot/TimeShot.csproj` and update `packaging/RELEASE_NOTES.md`.

To build the same zip locally, run `pwsh packaging/build.ps1` on Windows. It needs the .NET 8 SDK and Visual Studio (or its Build Tools) with the "Desktop development with C++" workload, which provides the Visual C++ runtime files that `lsl.dll` needs.

### Key Concepts

- LSL is only used when recording; no unnecessary traffic during previews.
- Each frame's marker is pushed the moment the camera delivers the frame, and LSL stamps it with its own clock right then. TimeShot never sets timestamps itself.
- Each camera's capture loop runs on a thread of its own and only grabs frames and pushes markers. Writing the video (encoding and disk I/O) happens on a separate writer thread per camera, and the preview is updated without waiting for the UI, so neither can delay the next marker.
- No recorded frame is ever dropped: frame N of the `.avi` always belongs to marker N. If the disk falls more than about two seconds behind, capture waits for it rather than skip frames.
- Previews show at most 20 frames per second; recordings keep the camera's full frame rate.

## Contributing

Pull requests and issues are welcome!

### Suggestions

- Enable audio recording from selected microphones.

## License

[GPL-2.0-only](https://opensource.org/licenses/GPL-2.0)

## Acknowledgments

- OpenCV community for the excellent C# wrapper.
- SCCN for LabStreamingLayer.
- MaterialSkin developers for the modern UI framework.

## Author

**Mark Span**  
GitHub: [@MarkSpan](https://github.com/MarkSpan)