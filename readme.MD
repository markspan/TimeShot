de# TimeShot

**TimeShot** is a Windows desktop application designed for synchronized multi-camera video capture with real-time LabStreamingLayer (LSL) integration. It allows users to record video from multiple USB cameras while simultaneously sending frame-accurate indices over LSL for synchronization in experimental setups (e.g., EEG, eye-tracking, behavioral experiments).

## Features

- **Multi-camera support**: Detects and manages multiple connected webcams.
- **Customizable**: Each camera has its own filename, stream name, and preview.
- **Real-time previews**: Each video stream is shown in a live preview window.
- **Synchronized recording**: Start recording from all selected cameras simultaneously.
- **LSL integration**: Sends frame indices as indices over LSL for each recorded frame.
- **Visual frame indexing**: Each recorded frame is annotated with its frame number.
- **User-friendly GUI**: Built with MaterialSkin for a modern look and feel.

## Technologies Used

- **.NET 8**
- **C# WinForms**
 **[OpenCvSharp](https://github.com/shimat/opencvsharp)** (for camera and video handling)
 **[LSL-CSharp](https://github.com/sccn/liblsl-CSharp)** (for marker streaming)

## Installation

### Prerequisites

- Windows 10 or higher
- [.NET 8 SDK](https://dotnet.microsoft.com/download)
- Visual Studio 2022 or later (with Windows Forms workload)

### Setup

1. Download the latest release from the GitHub repo.

2. Unzip.

3. run the TimeShot.exe.

## Usage

1. **Launch the application.**

2. The application will auto-detect available USB cameras.

3. For each detected camera:
   - Customize the stream name (used in LSL).
   - Choose an output filename for the video.
   - Toggle whether to include this camera.

4. Click **Create Streams**:
   - This initializes the preview windows and sets up streams.

5. Click **Start Recording**:
   - Recording begins for all selected cameras.
   - Each frame is saved to a local `.mp4` file and a marker with the current frame index is sent over LSL.

If the 'wait' togglebutton is ticked, the program will wait recording and streaming until the lab recorder is started.

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


### Key Concepts

- LSL is only used when recording; no unnecessary traffic during previews.

## Contributing

Pull requests and issues are welcome!

[This](https://github.com/markspan/MovieMerger) might be used to merge audio and video...

## License

[GPL-2.0-only](https://opensource.org/licenses/GPL-2.0)

## Acknowledgments

- OpenCV community for the excellent C# wrapper.
- SCCN for LabStreamingLayer.
- MaterialSkin developers for the modern UI framework.

## Author

**Mark Span**  
GitHub: [@MarkSpan](https://github.com/MarkSpan)
