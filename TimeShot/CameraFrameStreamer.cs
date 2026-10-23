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
    /// Captures and streams frames from a camera device: owns the OpenCV capture, the LSL
    /// marker outlet and, while recording, the <see cref="FrameWriter"/> of the video file.
    /// Its capture loop runs on a dedicated thread and does as little as possible: it pushes
    /// each frame's marker the moment the camera delivers the frame (LSL stamps it then), and
    /// leaves writing to the frame writer's thread and painting to the UI thread, so neither
    /// can delay the next marker. That loop is also the only place the capture and the outlet
    /// are released, so they are never released while it may still be using them.
    /// </summary>
    public class CameraFrameStreamer
    {
        private const int ConsumerPollIntervalMs = 200;
        private const int MaxWaitForConsumersSeconds = 3600;
        private const int CalibrationFrameCount = 15;

        // How long Stop() waits for the capture loop to finish before closing the preview
        // anyway. The loop still releases its resources itself once Grab() returns.
        private static readonly TimeSpan StopTimeout = TimeSpan.FromSeconds(5);

        // How long Stop() then waits for the frame writer to write its backlog and finalise
        // the file. The backlog is bounded (see FrameWriter), so this is generous.
        private static readonly TimeSpan WriterDrainTimeout = TimeSpan.FromSeconds(30);

        // How long the capture loop pauses after the camera failed to deliver a frame. It checks
        // for Stop() again right after, so this also bounds how long stopping waits for it.
        private const int NoFrameRetryDelayMs = 10;

        // At most 20 preview frames a second: plenty to aim a camera, and light on the UI thread.
        private static readonly TimeSpan MinPreviewInterval = TimeSpan.FromMilliseconds(50);

        private readonly int cameraIndex;
        private readonly string cameraName;
        private readonly string fileName;
        private readonly string streamName;
        private readonly StreamOutlet streamOutlet;
        private readonly VideoCapture capture;
        private readonly OpenCvSharp.Size captureSize;
        private readonly double fps;
        // Created by StartRecording(), so creating the streams never creates or truncates a file.
        // Set once, on the UI thread; the capture loop adds the frames and completes it.
        private volatile FrameWriter? frameWriter;
        private Task? captureTask;
        private CancellationTokenSource? cts;
        private volatile bool recording;
        // Preview throttling: the time of the last preview (capture thread only), and whether
        // a preview frame is still on its way to the UI thread (cleared there).
        private long lastPreviewTimestamp;
        private volatile bool previewPending;

        public CameraOutputForm OutputForm { get; }

        /// <summary>Indicates whether recording is active.</summary>
        public bool IsRecording => recording;

        /// <summary>
        /// Opens the camera, measures its real delivered resolution/fps, and starts the
        /// preview loop. Throws if the camera itself cannot be opened. The video file is not
        /// touched here: <see cref="StartRecording"/> creates it.
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

            captureSize = new OpenCvSharp.Size(probeFrame.Width, probeFrame.Height);
            var formSize = new System.Drawing.Size(probeFrame.Width, probeFrame.Height);
            fps = MeasureFps();

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

            OutputForm = new CameraOutputForm { Size = formSize };

            cts = new CancellationTokenSource();
            CancellationToken token = cts.Token;
            // LongRunning: the loop blocks in Grab() for the streamer's whole life, so it gets a
            // thread of its own, which also lets it raise that thread's priority.
            captureTask = Task.Factory.StartNew(() => CaptureLoop(token), CancellationToken.None,
                TaskCreationOptions.LongRunning, TaskScheduler.Default);
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
        /// The video file is created only once recording really starts.
        /// </summary>
        public async Task StartRecording(CheckState cs)
        {
            // Once Stop() has cancelled, the capture loop may already have closed the outlet and
            // the camera: every step below checks the token before touching them again.
            if (cts is not { } source || source.IsCancellationRequested)
                return;
            if (frameWriter is not null)
                return; // already started: one recording, and one file, per stream
            CancellationToken token = source.Token;

            if (!capture.IsOpened())
            {
                MessageBox.Show($"Failed to start session for camera {cameraIndex}: the camera is not open.");
                return;
            }

            if (cs == CheckState.Checked)
            {
                DateTime deadline = DateTime.UtcNow.AddSeconds(MaxWaitForConsumersSeconds);
                try
                {
                    while (true)
                    {
                        token.ThrowIfCancellationRequested();
                        if (streamOutlet.have_consumers())
                            break;
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

            if (token.IsCancellationRequested)
                return; // stopped meanwhile: do not create a file nobody would finalise

            var writer = FrameWriter.Create(fileName, fps, captureSize);
            if (writer is null)
            {
                MessageBox.Show($"Failed to start session for camera {cameraIndex}: could not create \"{fileName}\".");
                return;
            }

            frameWriter = writer;
            recording = true; // from the capture loop's next frame on, every frame is recorded
        }

        /// <summary>
        /// Stops recording and the capture loop, waits for the video file to be finalised, then
        /// closes the preview. The loop releases the camera and the LSL outlet itself as it
        /// exits, and completes the frame writer, which finalises the file on its own thread;
        /// releasing them here could pull them out from under a Grab/push still in flight on
        /// the loop's thread, which crashes in native code.
        /// </summary>
        public async Task Stop()
        {
            recording = false;
            cts?.Cancel();

            if (captureTask is { } loop && !await CompletesWithin(loop, StopTimeout))
            {
                // A camera that stopped delivering can leave Grab() blocked. The loop releases
                // everything, and lets the writer finalise the file, as soon as it returns; the
                // UI does not wait for that.
                Debug.WriteLine($"CameraFrameStreamer: camera {cameraIndex} did not stop within {StopTimeout.TotalSeconds:F0}s.");
            }
            else if (frameWriter is { } writer && !await CompletesWithin(writer.Completion, WriterDrainTimeout))
            {
                Debug.WriteLine($"CameraFrameStreamer: \"{fileName}\" was not finalised within {WriterDrainTimeout.TotalSeconds:F0}s.");
            }

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

        /// <summary>Whether <paramref name="task"/> finishes within <paramref name="timeout"/>; it keeps running otherwise.</summary>
        private static async Task<bool> CompletesWithin(Task task, TimeSpan timeout) =>
            await Task.WhenAny(task, Task.Delay(timeout)) == task;

        /// <summary>
        /// The capture loop, on its own thread from construction until <see cref="Stop"/>. While
        /// recording, frame N of the video file and marker N always belong together: every
        /// marker pushed gets a frame written, and frames are written in marker order.
        /// </summary>
        private void CaptureLoop(CancellationToken token)
        {
            // Timely wake-ups when a frame arrives keep the marker stamps close to the frames.
            // The thread is this loop's own (LongRunning), but restored anyway in case it is not.
            ThreadPriority priority = Thread.CurrentThread.Priority;
            Thread.CurrentThread.Priority = ThreadPriority.AboveNormal;

            // Each frame is decoded into `next`, so `current` still holds the last good frame
            // if decoding fails: that frame is then recorded again, under the new marker, rather
            // than leaving a marker without a frame. Black until the first frame arrives.
            using var bufferA = new Mat(captureSize, MatType.CV_8UC3, Scalar.Black);
            using var bufferB = new Mat();
            Mat current = bufferA, next = bufferB;
            int nextIndex = 0;

            try
            {
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        // Grab() returns as soon as the camera delivers a frame and leaves the
                        // decoding to Retrieve(), so the marker is pushed at delivery.
                        if (!capture.Grab())
                        {
                            Thread.Sleep(NoFrameRetryDelayMs);
                            continue;
                        }

                        FrameWriter? writer = recording ? frameWriter : null;
                        int index = -1;
                        if (writer is not null)
                        {
                            index = nextIndex++;
                            // The marker payload is the join key between "Nth frame written to
                            // the file" and wall-clock time -- what lets a later XDF import line
                            // up video frames with the rest of a recording. No timestamp is
                            // passed: LSL stamps the sample with its own clock as it is pushed.
                            streamOutlet.push_sample([index.ToString()]);
                        }

                        bool decoded = TryRetrieve(next);
                        if (decoded)
                            (current, next) = (next, current);

                        writer?.Add(index, current);
                        if (decoded)
                            UpdatePreview(current, index);
                    }
                    catch (Exception ex)
                    {
                        Debug.WriteLine($"CameraFrameStreamer: frame capture failed for camera {cameraIndex}: {ex.Message}");
                    }
                }
            }
            finally
            {
                // The only place these are released: after the loop's last use of them. The
                // writer writes what it still holds, then finalises the AVI (index and duration).
                recording = false;
                frameWriter?.Complete();
                capture.Release();
                streamOutlet.Close();
                Thread.CurrentThread.Priority = priority;
            }
        }

        /// <summary>Decodes the grabbed frame into <paramref name="frame"/>; false if that failed.</summary>
        private bool TryRetrieve(Mat frame)
        {
            try
            {
                return capture.Retrieve(frame) && !frame.Empty();
            }
            catch (OpenCVException ex)
            {
                Debug.WriteLine($"CameraFrameStreamer: decoding a frame of camera {cameraIndex} failed: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// Hands <paramref name="frame"/> to the preview window without waiting for the UI
        /// thread: at most one frame per <see cref="MinPreviewInterval"/>, and none while the
        /// previous one has not been shown yet, so a busy UI costs preview frames instead of
        /// delaying the capture loop. While recording, the preview shows the frame number too.
        /// </summary>
        private void UpdatePreview(Mat frame, int index)
        {
            if (previewPending || Stopwatch.GetElapsedTime(lastPreviewTimestamp) < MinPreviewInterval)
                return;
            if (OutputForm.IsDisposed || !OutputForm.IsHandleCreated)
                return;
            lastPreviewTimestamp = Stopwatch.GetTimestamp();

            Bitmap bitmap;
            if (index >= 0)
            {
                using var labelled = frame.Clone(); // `frame` may still be recorded again
                FrameWriter.DrawFrameNumber(labelled, index);
                bitmap = BitmapConverter.ToBitmap(labelled);
            }
            else
            {
                bitmap = BitmapConverter.ToBitmap(frame);
            }

            PictureBox pictureBox = OutputForm.pictureBox1;
            previewPending = true;
            try
            {
                pictureBox.BeginInvoke(() =>
                {
                    previewPending = false;
                    if (pictureBox.IsDisposed)
                    {
                        bitmap.Dispose();
                        return;
                    }
                    pictureBox.Image?.Dispose();
                    pictureBox.Image = bitmap;
                });
            }
            catch (Exception ex) when (ex is ObjectDisposedException or InvalidOperationException)
            {
                bitmap.Dispose(); // the preview is closing
                previewPending = false;
            }
        }
    }
}
