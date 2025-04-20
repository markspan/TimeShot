using OpenCvSharp;
using OpenCvSharp.Extensions;
using System;
using System.Drawing;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using LSL; // Assuming LSL namespace for StreamOutlet and related classes

namespace TimeShot
{
    /// <summary>
    /// Captures and streams frames from a camera device.
    /// </summary>
    public class CameraFrameStreamer
    {
        private readonly int cameraIndex;
        private readonly string fileName;
        private readonly string streamName;
        private readonly StreamOutlet streamOutlet;
        private readonly VideoWriter videoWriter;
        private readonly VideoCapture capture;
        private Task? captureTask;
        private CancellationTokenSource? cts;
        private CancellationTokenSource? consumerCheckCts;
        private bool recording = false;
        private int frameIndex = 0;
        public double frameRate = 0;

        /// <summary>
        /// Gets the output form associated with this frame streamer.
        /// </summary>
        public CameraOutputForm OutputForm { get; private set; }

        /// <summary>
        /// Indicates whether recording is active.
        /// </summary>
        public bool IsRecording => recording;

        /// <summary>
        /// Initializes the frame streamer and dynamically sets the form size based on capture resolution.
        /// </summary>
        /// <param name="index">The index of the camera to use.</param>
        /// <param name="file">The file name to write the video to.</param>
        /// <param name="stream">The name of the LSL stream.</param>
        /// <param name="outputForm">The output form to display the video frames.</param>
        public CameraFrameStreamer(int index, string file, string stream, CameraOutputForm outputForm)
        {
            cameraIndex = index;
            fileName = file;
            streamName = stream;
            OutputForm = outputForm;

            capture = new VideoCapture(cameraIndex);
            capture.Set(VideoCaptureProperties.BufferSize, 1); // Set buffer size to 1

            // Get actual camera resolution for dynamic form size adjustment
            frameRate = capture.Get(VideoCaptureProperties.Fps);
            var cameraWidth = (int)capture.Get(VideoCaptureProperties.FrameWidth);
            var cameraHeight = (int)capture.Get(VideoCaptureProperties.FrameHeight);

            var streamInfo = new StreamInfo(streamName, "Mocap", 1, frameRate, channel_format_t.cf_int32, Guid.NewGuid().ToString());
            streamOutlet = new StreamOutlet(streamInfo);

            videoWriter = new VideoWriter(
                fileName,
                FourCC.H264,
                frameRate,
                new OpenCvSharp.Size(cameraWidth, cameraHeight),
                true);
            OutputForm.Size = new System.Drawing.Size(cameraWidth, cameraHeight);

            cts = new CancellationTokenSource();
            captureTask = Task.Run(() => CaptureLoopAsync(cts.Token));
        }

        /// <summary>
        /// Begin recording to file and LSL.
        /// </summary>
        /// <param name="cs">The check state indicating whether to wait for consumers.</param>
        public async Task StartRecordingAsync(CheckState cs)
        {
            bool hasConsumers = true;
            if (cs == CheckState.Checked)
            {
                hasConsumers = await Task.Run(() => streamOutlet.wait_for_consumers(1200)); // Wait for 1200 seconds (20 minutes)
            }

            if (!videoWriter.IsOpened() || !capture.IsOpened() || !hasConsumers)
            {
                MessageBox.Show($"Failed to start session for camera {cameraIndex}.");
                return;
            }

            recording = true;

            // Start the consumer check task
            consumerCheckCts = new CancellationTokenSource();
            _ = Task.Run(() => CheckConsumersPeriodically(consumerCheckCts.Token))
                .ContinueWith(t =>
                {
                    if (t.Exception != null)
                    {
                        MessageBox.Show(t.Exception.Flatten().InnerException?.Message ?? "Unknown error in consumer check task.");
                    }
                }, TaskScheduler.FromCurrentSynchronizationContext());

        }
        /// <summary>
        /// Stop recording and release all resources.
        /// </summary>
        public void Stop()
        {
            try
            {
                recording = false;
                cts?.Cancel();
                capture?.Release();
                videoWriter?.Release();
                streamOutlet?.Close();

                if (OutputForm != null && !OutputForm.IsDisposed && OutputForm.IsHandleCreated)
                {
                    OutputForm.Invoke(new Action(() =>
                    {
                        if (!OutputForm.IsDisposed)
                        {
                            OutputForm.Close();
                        }
                    }));
                }
            }
            catch (TaskCanceledException)
            {
                // Handle task cancellation gracefully
                // Optionally log the cancellation or perform cleanup
            }
            catch (Exception ex)
            {
                // Handle other potential exceptions
                MessageBox.Show($"An error occurred: {ex.Message}");
            }
            finally
            {
                consumerCheckCts?.Cancel();
                cts = null;
                captureTask = null;
            }
        }



        /// <summary>
        /// Capture loop: updates preview and optionally records and streams.
        /// </summary>
        /// <param name="token">Cancellation token to stop the loop.</param>
        private async Task CaptureLoopAsync(CancellationToken token)
        {
            using var frame = new Mat();
            while (!token.IsCancellationRequested)
            {
                capture.Read(frame);
                if (frame.Empty())
                {
                    await Task.Delay(10, token);
                    continue;
                }
                if (frameRate != 0)
                    Cv2.PutText(frame, $"Fps: {Math.Round((float)frameRate)}", new OpenCvSharp.Point(10, 30),
                        HersheyFonts.HersheySimplex, .8, Scalar.Green, 2);

                if (recording)
                {
                    Cv2.PutText(frame, $"{frameIndex}", new OpenCvSharp.Point(10, 70),
                        HersheyFonts.HersheySimplex, 1, Scalar.Red, 2);

                    videoWriter.Write(frame);
                    streamOutlet.push_sample(new int[] { frameIndex });
                    frameIndex++;
                }

                var bmp = BitmapConverter.ToBitmap(frame);
                OutputForm?.pictureBox1?.Invoke(() =>
                {
                    OutputForm.pictureBox1.Image?.Dispose();
                    OutputForm.pictureBox1.Image = bmp;
                });

                await Task.Delay(1, token);
            }
        }

        /// <summary>
        /// Periodically checks for consumers and stops recording if none are found.
        /// </summary>
        /// <param name="token">Cancellation token to stop the checking loop.</param>
        private async Task CheckConsumersPeriodically(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                await Task.Delay(5000, token); // Check every 5 seconds

                if (!streamOutlet.have_consumers())
                {
                    recording = false;
                    Stop();
                    MessageBox.Show($"Recording stopped for camera {cameraIndex} due to no consumers.");
                    break;
                }
            }
        }

    }
}