# TimeShot

**TimeShot** is a Windows desktop application designed for synchronized multi-camera video capture with real-time LabStreamingLayer (LSL) integration. It allows users to record video from multiple USB cameras while simultaneously sending frame-accurate markers over LSL for synchronization in experimental setups (e.g., EEG, eye-tracking, behavioral experiments).

## Features

- **Multi-camera support**: Detects and manages multiple connected webcams.
- **Customizable**: Each camera has its own filename, stream name, and preview.
- **Real-time previews**: Each video stream is shown in a live preview window.
- **Synchronized recording**: Start recording from all selected cameras simultaneously.
- **LSL integration**: Sends frame indices as markers over LSL for each recorded frame.
- **Visual frame indexing**: Each recorded frame is annotated with its frame number.
- **User-friendly GUI**: Built with MaterialSkin for a modern look and feel.
- **Graceful shutdown**: Prompts for confirmation before stopping a recording to avoid data loss.
- **Task-based concurrency**: Modern, responsive asynchronous design using `Task` and `CancellationToken`.

## Technologies Used

- **.NET 8**
- **C# WinForms**
- **[OpenCvSharp](https://github.com/shimat/opencvsharp)** (for camera and video handling)
- **[LSL-CSharp](https://github.com/sccn/liblsl-CSharp)** (for marker streaming)
- **MaterialSkin** (for a modern UI look)

## Installation

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

4. Click **â€œCreate Streamsâ€**:
   - This initializes the preview windows and sets up streams.

5. Click **â€œStart Recordingâ€**:
   - Recording begins for all selected cameras.
   - Each frame is saved to a local `.mp4` file and a marker with the current frame index is sent over LSL.

6. Click **â€œStop Recordingâ€**:
   - You'll be asked to confirm before stopping.
   - All streams and previews are gracefully closed.

7. Click **â€œExit TimeShotâ€** to quit if no recording is active.

## Example Use Case

Suppose you are recording behavioral data using two webcams and an EEG cap. You can:

1. Set camera 0 as `"EyeStream"`, and camera 1 as `"BodyCam"`.
2. Record both streams simultaneously.
3. Use LSL markers sent by TimeShot to align video data precisely with EEG timestamps during post-processing.

## Development

### Project Structure

- `MainForm.cs` â€“ Handles UI logic and session orchestration.
- `CameraInfo.cs` â€“ Custom UI control representing a camera entry.
- `CameraSession.cs` â€“ Manages the lifecycle of a camera session.
- `CameraFrameStreamer.cs` â€“ Handles video capture, frame writing, LSL streaming, and preview.

### Key Concepts

- `CameraFrameStreamer` uses an async `CaptureLoopAsync()` Task per camera for responsiveness.
- LSL is only used when recording; no unnecessary traffic during previews.
- Cancellation is handled using `CancellationTokenSource` and clean resource disposal.

## Troubleshooting

- **Preview doesn't show**: Ensure the camera is not in use by another application.
- **No LSL stream detected**: Use the [LabRecorder](https://github.com/sccn/labstreaminglayer) or `lslview` to monitor active streams.
- **Video not saving**: Ensure write permissions to the working directory.

## Contributing

Pull requests and issues are welcome!

### Suggestions

- Add configurable frame rates and resolutions.
- Enable audio recording from selected microphones.
- Export session metadata (e.g., timestamps, camera info).

## License

[GPL-2.0-only](https://opensource.org/licenses/GPL-2.0)

## Acknowledgments

- OpenCV community for the excellent C# wrapper.
- SCCN for LabStreamingLayer.
- MaterialSkin developers for the modern UI framework.

## Author

**Mark Span**  
GitHub: [@MarkSpan](https://github.com/MarkSpan)