using LSL;
using OpenCvSharp;
using OpenCvSharp.Extensions;
using System.Diagnostics;
using System.Drawing;
using System.Threading;
using System.Windows.Forms;

namespace TimeShot
{
    /// <summary>
    /// Captures and streams frames from a camera device: owns the OpenCV capture, the
    /// output video file and the LSL marker outlet, and runs its own capture loop on a
    /// background task.
    /// </summary>
    public class CameraFrameStreamer
    {
        private const int ConsumerPollIntervalMs = 200;
        private const int MaxWaitForConsumersSeconds = 3600;
        private const int CalibrationFrameCount = 15;

        // Leaves room for the UI thread between frames.
        private const int LoopIdleDelayMs = 1;

        private readonly int cameraIndex;
        private readonly string cameraName;
        private readonly string fileName;
        private readonly string streamName;
        private readonly StreamOutlet streamOutlet;
        private readonly VideoCapture capture;
        private readonly VideoWriter? videoWriter;
        private Task? captureTask;
        private CancellationTokenSource? cts;
        private volatile bool recording;
        private int frameIndex;

        public CameraOutputForm OutputForm { get; }

        /// <summary>Indicates whether recording is active.</summary>
        public bool IsRecording => recording;

        /// <summary>
        /// Opens the camera, measures its real delivered resolution/fps, and starts the
        /// preview loop. Throws if the camera itself cannot be opened; a video-writer that
        /// fails to open is tolerated here (preview still works) and reported when
        /// <see cref="StartRecording"/> is actually called.
        /// </summary>
        public CameraFrameStreamer(int index, string name, string file, string stream)
        {
            cameraIndex = index;
            cameraName = name;
            fileName = file;
            streamName = stream;

            capture = new VideoCapture(cameraIndex);
            capture.Set(VideoCaptureProperties.BufferSize, 1); // reduces latency vs. the driver's own buffer
            if (!capture.IsOpened())
            {
                capture.Dispose();
                throw new InvalidOperationException($"Camera {cameraIndex} could not be opened.");
            }

            using var probeFrame = new Mat();
            capture.Read(probeFrame);
            if (probeFrame.Empty())
            {
                capture.Dispose();
                throw new InvalidOperationException($"Camera {cameraIndex} opened but delivered no frame.");
            }

            var captureSize = new OpenCvSharp.Size(probeFrame.Width, probeFrame.Height);
            var formSize = new System.Drawing.Size(probeFrame.Width, probeFrame.Height);
            double fps = MeasureFps();

            // Must be stable across restarts of *this* camera so a recorder can recover the
            // stream after a crash by re-finding a source with the same id (see the source_id
            // doc on StreamInfo in LSL.cs) -- a fresh GUID every run defeats that.
            string sourceId = $"{Environment.MachineName}-cam{cameraIndex}-{streamName}";
            // 0.0 = irregular rate: actual delivered frame timing isn't metronomic even at a
            // fixed nominal fps (named as LSL.LSL.IRREGULAR_RATE, but the namespace/class both
            // being called "LSL" makes that ambiguous from here).
            var streamInfo = new StreamInfo(streamName, "Markers", 1, 0.0, channel_format_t.cf_string, sourceId);

            XMLElement desc = streamInfo.desc();
            XMLElement channelElement = desc.append_child("channels").append_child("channel");
            channelElement.append_child_value("label", "FrameNumber");
            channelElement.append_child_value("type", "Marker");
            desc.append_child_value("video_file", fileName);
            desc.append_child_value("camera_name", cameraName);
            desc.append_child_value("measured_fps", fps.ToString("F2"));

            streamOutlet = new StreamOutlet(streamInfo);

            // MJPG, not H264: OpenCV's H264 writer needs the separate OpenH264 DLL next to the
            // exe and just silently fails to open without it (see the IsOpened() check below).
            // MJPG ships with OpenCV's own FFmpeg backend everywhere, and -- more importantly
            // for this tool -- every frame is independently encoded (no GOPs/B-frames), so
            // seeking to exactly "frame N" (the join key the LSL marker stream uses) is exact.
            //
            // Real captured resolution/fps, not a hardcoded guess: a writer opened at the
            // wrong size relative to what capture.Read() actually delivers corrupts frames.
            videoWriter = new VideoWriter(fileName, FourCC.MJPG, fps, captureSize, true);
            if (!videoWriter.IsOpened())
                Debug.WriteLine($"CameraFrameStreamer: video writer for camera {cameraIndex} failed to open ({fileName}).");

            OutputForm = new CameraOutputForm { Size = formSize };

            cts = new CancellationTokenSource();
            captureTask = Task.Run(() => CaptureLoopAsync(cts.Token));
        }

        private double MeasureFps()
        {
            using var frame = new Mat();
            var stopwatch = Stopwatch.StartNew();
            for (int i = 0; i < CalibrationFrameCount; i++)
                capture.Read(frame);
            stopwatch.Stop();

            return stopwatch.ElapsedMilliseconds > 0
                ? Math.Round(CalibrationFrameCount * 1000.0 / stopwatch.ElapsedMilliseconds)
                : 30;
        }

        /// <summary>
        /// Begin recording to file and LSL. If <paramref name="cs"/> is checked, waits
        /// (without blocking the caller's thread) for an LSL consumer to connect, polling
        /// <see cref="StreamOutlet.have_consumers"/> so the wait can be cancelled by
        /// <see cref="Stop"/> instead of blocking on the native library's own timeout.
        /// </summary>
        public async Task StartRecording(CheckState cs)
        {
            if (!capture.IsOpened() || videoWriter is not { } writer || !writer.IsOpened())
            {
                MessageBox.Show($"Failed to start session for camera {cameraIndex}: capture or output file not ready.");
                return;
            }

            if (cs == CheckState.Checked && !streamOutlet.have_consumers())
            {
                CancellationToken token = cts?.Token ?? CancellationToken.None;
                DateTime deadline = DateTime.UtcNow.AddSeconds(MaxWaitForConsumersSeconds);
                try
                {
                    while (!streamOutlet.have_consumers())
                    {
                        if (DateTime.UtcNow >= deadline)
                        {
                            MessageBox.Show($"No LSL consumer connected to camera {cameraIndex}'s stream " +
                                $"within {MaxWaitForConsumersSeconds}s; recording not started.");
                            return;
                        }
                        await Task.Delay(ConsumerPollIntervalMs, token);
                    }
                }
                catch (OperationCanceledException)
                {
                    return; // Stop() was requested while waiting for a consumer
                }
            }

            recording = true;
        }

        /// <summary>
        /// Stops recording and the capture loop, then releases all resources. Waits
        /// (bounded) for the loop to actually observe cancellation before releasing the
        /// capture/writer it may still be using, rather than releasing them out from under it.
        /// </summary>
        public async Task Stop()
        {
            recording = false;
            cts?.Cancel();

            if (captureTask != null)
                await Task.WhenAny(captureTask, Task.Delay(TimeSpan.FromSeconds(2)));

            capture.Release();
            videoWriter?.Release();
            streamOutlet.Close();

            if (!OutputForm.IsDisposed)
            {
                if (OutputForm.IsHandleCreated)
                    OutputForm.Invoke(OutputForm.Close);
                else
                    OutputForm.Close();
            }

            cts = null;
            captureTask = null;
        }

        private async Task CaptureLoopAsync(CancellationToken token)
        {
            using var frame = new Mat();

            try
            {
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        capture.Read(frame);
                        if (frame.Empty())
                        {
                            await Task.Delay(10, token);
                            continue;
                        }

                        if (recording)
                        {
                            int index = Interlocked.Increment(ref frameIndex) - 1;
                            Cv2.PutText(frame, $"{index}", new OpenCvSharp.Point(10, 30),
                                HersheyFonts.HersheySimplex, .75, Scalar.Red, 2);
                            videoWriter?.Write(frame);

                            // The marker payload is the join key between "Nth frame written to
                            // the file" and wall-clock time -- what lets a later XDF import line
                            // up video frames with the rest of a recording.
                            streamOutlet.push_sample([index.ToString()]);
                        }

                        ShowFrame(BitmapConverter.ToBitmap(frame));
                    }
                    catch (OperationCanceledException)
                    {
                        throw; // shutdown in progress -- let the outer catch handle it
                    }
                    catch (Exception ex)
                    {
                        Debug.WriteLine($"CameraFrameStreamer: frame capture failed for camera {cameraIndex}: {ex.Message}");
                    }

                    await Task.Delay(LoopIdleDelayMs, token);
                }
            }
            catch (OperationCanceledException)
            {
                // Expected on Stop().
            }
        }

        private void ShowFrame(Bitmap bmp)
        {
            if (OutputForm.IsDisposed || !OutputForm.IsHandleCreated)
            {
                bmp.Dispose();
                return;
            }

            try
            {
                OutputForm.pictureBox1.Invoke(() =>
                {
                    OutputForm.pictureBox1.Image?.Dispose();
                    OutputForm.pictureBox1.Image = bmp;
                });
            }
            catch (Exception ex) when (ex is ObjectDisposedException or InvalidOperationException)
            {
                bmp.Dispose();
            }
        }
    }
}
