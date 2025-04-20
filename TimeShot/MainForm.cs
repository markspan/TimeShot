using MaterialSkin.Controls;
using LSL;                      // labstreaminglayer
using OpenCvSharp;              // for videocapture and videowriter
using OpenCvSharp.Extensions;
using System.DirectoryServices;   // for bitmapconverter
using DirectShowLib;

namespace TimeShot
{

    public partial class MainForm : MaterialForm
    {
        public static List<string> GetCameraNames()
        {
            var cameraNames = new List<string>();

            DsDevice[] systemCameras = DsDevice.GetDevicesOfCat(FilterCategory.VideoInputDevice);
            foreach (DsDevice cam in systemCameras)
            {
                cameraNames.Add(cam.Name);
            }

            return cameraNames;
        }

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
            var cameraNames = GetCameraNames();

            for (int i = 0; i < cameraNames.Count; i++)
            {
                using var capture = new VideoCapture(i);
                capture.Set(VideoCaptureProperties.BufferSize, 1);

                if (capture.IsOpened())
                {
                    CameraInfo cam = new();
                    cam.Check.Checked = true;
                    cam.CamName.Text = cameraNames[i]; // Use the friendly name!
                    cam.FileName.Text = $"{cameraNames[i].Replace(" ", "_")}.mp4";
                    cam.StreamName.Text = $"{cameraNames[i].Replace(" ", "_")}_Stream";
                    cam.Size = new System.Drawing.Size(1024, 52);
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
        private async void StreamButton_Click(object sender, EventArgs e)
        {
            StreamButton.Enabled = false;
            foreach (var session in cameraSessions)
            {
                await session.StartAsync(WaitForConsumers.CheckState);
            }
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
            try
            {
                foreach (var session in cameraSessions)
                {
                    session.Stop();
                }
            }
            catch (TaskCanceledException)
            {
                // Handle task cancellation gracefully
            }
            catch (Exception ex)
            {
                // Handle other potential exceptions
                MessageBox.Show($"An error occurred while closing the application: {ex.Message}");
            }
        }

    }

    /// <summary>
    /// Represents a single camera session.
    /// </summary>
    public class CameraSession
    {
        private readonly CameraFrameStreamer frameStreamer;

        /// <summary>
        /// Gets the output form associated with this camera session.
        /// </summary>
        public CameraOutputForm OutputForm => frameStreamer.OutputForm;

        public CameraSession(int index, string file, string stream)
        {
            var outputForm = new CameraOutputForm(); // Instantiate the output form
            frameStreamer = new CameraFrameStreamer(index, file, stream, outputForm);
        }

        /// <summary>
        /// Start recording asynchronously.
        /// </summary>
        public async Task StartAsync(CheckState cs) => await frameStreamer.StartRecordingAsync(cs);

        /// <summary>
        /// Stop recording.
        /// </summary>
        public void Stop() => frameStreamer.Stop();

        /// <summary>
        /// Indicates whether the session is currently recording.
        /// </summary>
        public bool IsRecording => frameStreamer.IsRecording;
    }
}