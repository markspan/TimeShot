using MaterialSkin.Controls;
using LSL;                      // labstreaminglayer
using OpenCvSharp;              // for videocapture and videowriter
using OpenCvSharp.Extensions;   // for bitmapconverter

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
        /// Stop button with contextual behavior.
        /// </summary>
        private void StopButton_Click(object sender, EventArgs e)
        {
            if (cameraSessions.Any(s => s.IsRecording))
            {
                // Case 1: Stop recording and streaming
                foreach (var session in cameraSessions)
                    session.Stop();

                cameraSessions.Clear();
                CreateStreamButton.Enabled = true;
                StreamButton.Enabled = true;
                StopButton.Text = "Exit TimeShot";
            }
            else if (cameraSessions.Count > 0)
            {
                // Case 2: Stop previews (no recording yet)
                foreach (var session in cameraSessions)
                    session.Stop();

                cameraSessions.Clear();
                CreateStreamButton.Enabled = true;
                StreamButton.Enabled = true;
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
        /// Initializes the frame streamer.
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
        public void Stop()
        {
            running = false;
            capture.Release();
            videoWriter.Release();
            streamOutlet.Close();
            OutputForm?.Invoke(() => OutputForm.Close());
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
                    await Task.Delay(10, token);  // avoid busy looping
                    continue;
                }

                // If recording, write to file and send LSL marker
                if (recording)
                {
                    // Draw frame index
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


                // Optional throttle to reduce CPU, adjust if needed
                await Task.Delay(1, token);
            }
        }

    }
}
