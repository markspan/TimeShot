using MaterialSkin.Controls;
using OpenCvSharp;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;
using System.IO;

namespace TimeShot
{
    public partial class MainForm : MaterialForm
    {
        readonly MaterialSkin.MaterialSkinManager materialSkinManager;
        private readonly List<CameraSession> cameraSessions = new();
        AppConfig config = new();

        public MainForm()
        {
            InitializeComponent();
            var exeDir = AppDomain.CurrentDomain.BaseDirectory;
            var yamlPath = Path.Combine(exeDir, "config.yaml");

            if (File.Exists(yamlPath))
            {
                try
                {
                    var yamlText = File.ReadAllText(yamlPath);
                    var deserializer = new DeserializerBuilder()
                        .WithNamingConvention(CamelCaseNamingConvention.Instance)
                        .Build();

                    config = deserializer.Deserialize<AppConfig>(yamlText);
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"Error reading YAML file:\n{ex.Message}",
                        "Configuration Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }

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
                capture.Set(VideoCaptureProperties.BufferSize, 1); // Set buffer size to 1
                if (capture.IsOpened())
                {
                    CameraInfo cam = new();
                    cam.Tag = i; // the actual device index -- CameraBox.Controls order isn't it once cameras get unchecked
                    cam.Check.Checked = true;
                    var SessionId = Session.Text;
                    // Replace {i} placeholders with actual index
                    string camName = config.CameraName.Replace("{i}", i.ToString());
                    string streamName = config.StreamName.Replace("{i}", i.ToString());
                    string videoFile = config.VideoFile.Replace("{i}", i.ToString());

                    cam.CamName.Text = camName.Replace("{s}", SessionId);
                    cam.FileName.Text = videoFile.Replace("{s}", SessionId);
                    cam.StreamName.Text = streamName.Replace("{s}", SessionId);

                    cam.Size = new System.Drawing.Size(775, 52);
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
            CreateStreamButton.Enabled = false;

            foreach (Control control in CameraBox.Controls)
            {
                if (control is not CameraInfo camInfo || !camInfo.Check.Checked)
                    continue;

                int cameraIndex = (int)camInfo.Tag!; // always set in GetAvailableCameras()
                try
                {
                    var session = new CameraSession(cameraIndex, camInfo.CamName.Text, camInfo.FileName.Text, camInfo.StreamName.Text);
                    session.OutputForm.Show();
                    cameraSessions.Add(session);
                }
                catch (Exception ex)
                {
                    MessageBox.Show($"Camera {cameraIndex} (\"{camInfo.CamName.Text}\") could not be started:\n{ex.Message}",
                        "Camera error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }

            if (cameraSessions.Count > 0)
            {
                StreamButton.Enabled = true;
                StopButton.Text = "Close Streams";
            }
            else
            {
                CreateStreamButton.Enabled = true; // nothing started -- allow retry
            }
        }

        /// <summary>
        /// Start recording for all active camera sessions.
        /// </summary>
        private async void StreamButton_Click(object sender, EventArgs e)
        {
            StreamButton.Enabled = false;
            StopButton.Text = "Stop Recording";
            await Task.WhenAll(cameraSessions.Select(s => s.Start(WaitForConsumers.CheckState)));
        }

        /// <summary>
        /// Stop button with contextual behavior: stop recording, close streams, or exit.
        /// </summary>
        private async void StopButton_Click(object sender, EventArgs e)
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
            }
            else if (cameraSessions.Count == 0)
            {
                // Case: nothing to stop or close -- exit the application.
                Close();
                return;
            }

            // Stop recording (if any) and close streams/previews.
            await Task.WhenAll(cameraSessions.Select(s => s.Stop()));

            cameraSessions.Clear();
            CreateStreamButton.Enabled = true;
            StreamButton.Enabled = false;
            StopButton.Text = "Exit TimeShot";
        }

        private void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            Task.WhenAll(cameraSessions.Select(s => s.Stop())).GetAwaiter().GetResult();
        }

        private void Session_KeyPress(object sender, KeyPressEventArgs e)
        {
            // Allow control keys such as Backspace
            if (!char.IsControl(e.KeyChar) && !char.IsDigit(e.KeyChar))
            {
                e.Handled = true;  // block the key
            }
        }

        private void Session_TextChanged(object sender, EventArgs e)
        {
            string sessionId = Session.Text;

            int i = 0;
            foreach (Control control in CameraBox.Controls)
            {
                if (control is CameraInfo cam)
                {
                    cam.CamName.Text = config.CameraName
                        .Replace("{i}", i.ToString())
                        .Replace("{s}", sessionId);

                    cam.StreamName.Text = config.StreamName
                        .Replace("{i}", i.ToString())
                        .Replace("{s}", sessionId);

                    cam.FileName.Text = config.VideoFile
                        .Replace("{i}", i.ToString())
                        .Replace("{s}", sessionId);

                    i++;
                }
            }
        }
    }
}
