using MaterialSkin.Controls;
using LSL;                      // labstreaminglayer
using OpenCvSharp;              // for videocapture and videowriter
using OpenCvSharp.Extensions;
using System.DirectoryServices;   // for bitmapconverter

namespace TimeShot
{
    public partial class MainForm : MaterialForm
    {
        readonly MaterialSkin.MaterialSkinManager materialSkinManager;
        private readonly List<CameraSession> cameraSessions = new();

        public MainForm(string[] args)
        {
            InitializeComponent();

            materialSkinManager = MaterialSkin.MaterialSkinManager.Instance;
            materialSkinManager.AddFormToManage(this);
            materialSkinManager.Theme = MaterialSkin.MaterialSkinManager.Themes.LIGHT;
            materialSkinManager.ColorScheme = new MaterialSkin.ColorScheme(
                MaterialSkin.Primary.Blue400, MaterialSkin.Primary.Blue500,
                MaterialSkin.Primary.Blue500, MaterialSkin.Accent.LightBlue200,
                MaterialSkin.TextShade.WHITE
            );
            GetAvailableCameras();
        }

        /// <summary>
        /// Detect available cameras and add them to the CameraBox control.
        /// </summary>
        private void GetAvailableCameras()
        {
            for (int i = 0; i < 10; i++)
            {
                using var capture = new VideoCapture(i);
                if (capture.IsOpened())
                {
                    CameraInfo cam = new();
                    cam.Check.Checked = true;
                    cam.CamName.Text = $"Camera {i}";
                    cam.FileName.Text = $"Cam{i}.mp4";
                    cam.StreamName.Text = $"Cam{i}_Stream";
                    cam.Size = new System.Drawing.Size(569, 52);
                    cam.Location = new System.Drawing.Point(9, 3 + (54 * i));
                    CameraBox.Controls.Add(cam);
                }
            }
        }

        /// <summary>
        /// Create camera sessions and preview windows for selected cameras.
        /// </summary>
        private void CreateStreamButton_Click(object sender, EventArgs e)
        {
            cameraSessions.Clear();
            int cameraIndex = 0;
            CreateStreamButton.Enabled = false;
            foreach (Control control in CameraBox.Controls)
            {
                if (control is CameraInfo camInfo && camInfo.Check.Checked)
                {
                    var session = new CameraSession(
                        cameraIndex,
                        camInfo.FileName.Text,
                        camInfo.StreamName.Text
                    );

                    session.OutputForm.Show();
                    cameraSessions.Add(session);
                    cameraIndex++;
                }
            }

            if (cameraSessions.Count > 0)
            {
                CreateStreamButton.Enabled = false;
                StreamButton.Enabled = true;
                StopButton.Text = "Close Streams";
            }
        }

        /// <summary>
        /// Start recording for all active camera sessions.
        /// </summary>
        private void StreamButton_Click(object sender, EventArgs e)
        {
            foreach (var session in cameraSessions)
                session.Start();

            StreamButton.Enabled = false;
            StopButton.Text = "Stop Recording";
        }

        /// <summary>
        /// Stop button with contextual behavior: stop recording, close streams, or exit.
        /// </summary>
        private void StopButton_Click(object sender, EventArgs e)
        {
            if (cameraSessions.Any(s => s.IsRecording))
            {
                var confirmResult = MessageBox.Show(
                    "Recording is in progress. Are you sure you want to stop?",
                    "Confirm Stop Recording",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question);

                if (confirmResult == DialogResult.No)
                    return;

                // Case 1: Stop recording and streaming
                foreach (var session in cameraSessions)
                    session?.Stop();

                cameraSessions.Clear();
                CreateStreamButton.Enabled = true;
                StreamButton.Enabled = false;
                StopButton.Text = "Exit TimeShot";
            }
            else if (cameraSessions.Count > 0)
            {
                // Case 2: Stop previews (no recording yet)
                foreach (var session in cameraSessions) 
                    session.Stop();
                    

                cameraSessions.Clear();
                CreateStreamButton.Enabled = true;
                StreamButton.Enabled = false;
                StopButton.Text = "Exit TimeShot";
            }
            else
            {
                // Case 3: Exit application
                Close();
            }
        }

        private void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            foreach (var session in cameraSessions)
                session.Stop();
        }
    }

    /// <summary>
    /// Represents a single camera session.
    /// </summary>
    public class CameraSession
    {
        private readonly CameraFrameStreamer frameStreamer;
        public CameraOutputForm OutputForm => frameStreamer.OutputForm;

        public CameraSession(int index, string file, string stream)
        {
            frameStreamer = new CameraFrameStreamer(index, file, stream);
        }

        /// <summary>
        /// Start recording.
        /// </summary>
        public void Start() => frameStreamer.StartRecording();

        /// <summary>
        /// Stop recording.
        /// </summary>
        public void Stop() => frameStreamer.Stop();

        /// <summary>
        /// Indicates whether the session is currently recording.
        /// </summary>
        public bool IsRecording => frameStreamer.IsRecording;
    }

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
        private bool recording = false;
        private bool running = true;
        private int frameIndex = 0;

        public CameraOutputForm OutputForm { get; private set; }

        /// <summary>
        /// Indicates whether recording is active.
        /// </summary>
        public bool IsRecording => recording;

        /// <summary>
        /// Initializes the frame streamer and dynamically sets the form size based on capture resolution.
        /// </summary>
        public CameraFrameStreamer(int index, string file, string stream)
        {
            cameraIndex = index;
            fileName = file;
            streamName = stream;

            var streamInfo = new StreamInfo(streamName, "Markers", 1, 0, channel_format_t.cf_int64, Guid.NewGuid().ToString());
            streamOutlet = new StreamOutlet(streamInfo);

            videoWriter = new VideoWriter(
                fileName,
                FourCC.H264,
                30,
                new OpenCvSharp.Size(640, 480),
                true);

            capture = new VideoCapture(cameraIndex);
            OutputForm = new CameraOutputForm();

            // Get actual camera resolution for dynamic form size adjustment
            var cameraWidth = (int)capture.Get(3);
            var cameraHeight = (int)capture.Get(4);
            OutputForm.Size = new System.Drawing.Size(cameraWidth, cameraHeight);

            cts = new CancellationTokenSource();
            captureTask = Task.Run(() => CaptureLoopAsync(cts.Token));
        }

        /// <summary>
        /// Begin recording to file and LSL.
        /// </summary>
        public void StartRecording()
        {
            if (!videoWriter.IsOpened() || !capture.IsOpened())
            {
                MessageBox.Show($"Failed to start session for camera {cameraIndex}.");
                return;
            }
            recording = true;
        }

        /// <summary>
        /// Stop recording and release all resources.
        /// </summary>
        /// <summary>
        /// Stops the recording process and releases associated resources. 
        /// This method cancels the capture loop, disables recording, and 
        /// ensures resources are released. It does not block the UI thread.
        /// </summary>
        public void Stop()
        {
            // Set the recording flag to false — this ensures no further frames are written.
            recording = false;

            // Request cancellation of the capture loop.
            cts?.Cancel();

            // Do not use captureTask.Wait() — it would block the main thread.
            // Instead, allow the capture loop to gracefully exit on its own.

            // Release OpenCV resources — safe to call even if already released.
            capture?.Release();
            videoWriter?.Release();

            // Close the LSL outlet — no more samples will be sent.
            streamOutlet?.Close();

            // Close the output form from the UI thread, if it exists.
            OutputForm?.Invoke(() =>
            {
                OutputForm?.Close();
            });

            // Nullify the cancellation token source so it can be recreated on next start.
            cts = null;

            // Nullify the capture task to indicate it's no longer running.
            captureTask = null;
        }



        /// <summary>
        /// Capture loop: updates preview and optionally records and streams.
        /// </summary>
        private async Task CaptureLoopAsync(CancellationToken token)
        {
            using var frame = new Mat();

            while (!token.IsCancellationRequested)
            {
                capture.Read(frame);
                if (frame.Empty())
                {
                    // If frame is empty, wait briefly to avoid CPU overload
                    await Task.Delay(10, token);
                    continue;
                }

                // If recording, write to file and send LSL marker
                if (recording)
                {
                    // Draw frame index as a marker on the frame
                    Cv2.PutText(frame, $"{frameIndex}", new OpenCvSharp.Point(10, 30),
                        HersheyFonts.HersheySimplex, 1, Scalar.Red, 2);
                    videoWriter.Write(frame);
                    streamOutlet.push_sample(new int[] { frameIndex });
                    frameIndex++;
                }

                // Show frame in UI
                var bmp = BitmapConverter.ToBitmap(frame);
                OutputForm?.pictureBox1?.Invoke(() =>
                {
                    OutputForm.pictureBox1.Image?.Dispose();
                    OutputForm.pictureBox1.Image = bmp;
                });

                // Allow cancellation to be detected frequently
                await Task.Delay(1, token);
            }
        }

    }
}
