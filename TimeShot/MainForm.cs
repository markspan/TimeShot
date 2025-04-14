using MaterialSkin.Controls;
using LSL;                      // LabStreamingLayer
using OpenCvSharp;              // For VideoCapture and VideoWriter
using OpenCvSharp.Extensions;   // For BitmapConverter

namespace TimeShot
{
    public partial class MainForm : MaterialForm
    {
        readonly MaterialSkin.MaterialSkinManager materialSkinManager;
        //private readonly string[]? cameraList = null;
        //private readonly List<CameraInfo>? cameraControls = null;
        //private string? dataPath = null;
        private List<StreamInfo>? streamInfo = null;
        private List<StreamOutlet>? streamOutlet = null;
        private bool isStreaming = false;
        private VideoWriter? videoWriter;
        private CameraFrameStreamer cameraStreamer;
        private CameraOutputForm cameraOutputForm;
        private int frameIndex = 0;


        /// <summary>
        /// Initializes the MainForm and configures the MaterialSkin theme.
        /// </summary>
        /// <param name="args">Command-line arguments (unused)</param>
        public MainForm(string[] args)
        {
            InitializeComponent();
            materialSkinManager = MaterialSkin.MaterialSkinManager.Instance;
            materialSkinManager.AddFormToManage(this);
            materialSkinManager.Theme = MaterialSkin.MaterialSkinManager.Themes.LIGHT;
            materialSkinManager.ColorScheme = new MaterialSkin.ColorScheme(
                MaterialSkin.Primary.Blue400, MaterialSkin.Primary.Blue500,
                MaterialSkin.Primary.Blue500, MaterialSkin.Accent.LightBlue200,
                MaterialSkin.TextShade.WHITE);

            GetAvailableCameras();
            cameraStreamer = new CameraFrameStreamer();
            cameraStreamer.FrameReady += OnFrameReady;
        }

        /// <summary>
        /// Starts streaming video from the specified camera index.
        /// </summary>
        /// <param name="cameraIndex">Index of the camera to stream from</param>
        private void StartStreaming(int cameraIndex)
        {
            cameraStreamer?.Stop();
            cameraStreamer = new CameraFrameStreamer();
            cameraStreamer.FrameReady += OnFrameReady;
            cameraStreamer.Start(cameraIndex);
        }

        /// <summary>
        /// Stops video streaming.
        /// </summary>
        /// <param name="cameraIndex">Camera index (currently unused)</param>
        private void StopStreaming(int cameraIndex)
        {
            cameraStreamer?.Stop();
        }

        /// <summary>
        /// Called when the form is closing. Stops any active camera streams.
        /// </summary>
        private void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            cameraStreamer?.Stop();
        }

        /// <summary>
        /// Detects and displays available cameras by probing up to 10 indices.
        /// </summary>
        private void GetAvailableCameras()
        {
            for (int i = 0; i < 10; i++)
            {
                using (var capture = new VideoCapture(i))
                {
                    if (capture.IsOpened())
                    {
                        CameraInfo cam = new CameraInfo
                        {
                            Size = new System.Drawing.Size(569, 52),
                            Location = new System.Drawing.Point(9, 3 + (54 * i))
                        };
                        cam.Check.Checked = true;
                        cam.CamName.Text = $"Camera {i}";
                        cam.FileName.Text = $"Cam{i}.mp4";
                        cam.StreamName.Text = $"Cam{i}_Stream";
                        CameraBox.Controls.Add(cam);
                    }
                }
            }
        }

        /// <summary>
        /// Creates LSL streams for selected cameras and starts preview output.
        /// </summary>
        private void CreateStreamButton_Click(object sender, EventArgs e)
        {
            foreach (Control control in CameraBox.Controls)
            {
                streamInfo = [];
                streamOutlet = [];

                if (control is CameraInfo cameraInstance && cameraInstance.Check.Checked)
                {
                    string streamName = cameraInstance.StreamName.Text;
                    // string fileName = cameraInstance.FileName.Text;

                    var si = new StreamInfo(streamName, "Markers", 1, 0, channel_format_t.cf_int64, DateTime.Now.ToString());
                    streamInfo.Add(si);
                    streamOutlet.Add(new StreamOutlet(si));
                }

                CreateStreamButton.Enabled = false;
                cameraOutputForm = new CameraOutputForm();
                cameraOutputForm.Size = new System.Drawing.Size(640, 480);
                cameraOutputForm.Show();
                isStreaming = false;
                StartStreaming(0);
            }
        }

        /// <summary>
        /// Stops streaming and recording, or closes streams and video writers.
        /// </summary>
        private void StopButton_Click(object sender, EventArgs e)
        {
            if (!StreamButton.Enabled)
            {
                StreamButton.Enabled = true;
                StopStreaming(0);
                cameraOutputForm?.Close();
                return;
            }

            if (!CreateStreamButton.Enabled)
            {
                CreateStreamButton.Enabled = true;
                if (streamInfo != null && streamOutlet != null)
                {
                    foreach (var stream in streamInfo) stream.Dispose();
                    foreach (var stream in streamOutlet) stream.Dispose();
                }

                streamInfo?.Clear();
                streamOutlet?.Clear();
                StopRecording();
            }
        }

        /// <summary>
        /// Begins video recording and stream output for selected cameras.
        /// </summary>
        private void StreamButton_Click(object sender, EventArgs e)
        {
            isStreaming = true;
            foreach (Control control in CameraBox.Controls)
            {
                if (control is CameraInfo cameraInstance && cameraInstance.Check.Checked)
                {
                    string fileName = cameraInstance.FileName.Text;
                    StartRecording(fileName);
                    StreamButton.Enabled = false;
                }
            }
        }

        /// <summary>
        /// Delegates frame handling to overloaded method with display output.
        /// </summary>
        private void OnFrameReady(Mat frame)
        {
            if (cameraOutputForm == null) return;
            OnFrameReady(frame, cameraOutputForm);
        }

        /// <summary>
        /// Starts the video writer for saving frames to disk.
        /// </summary>
        /// <param name="fileName">Output file path for video</param>
        private void StartRecording(string fileName)
        {
            videoWriter = new VideoWriter(
                fileName,
                FourCC.H264,
                30,
                new OpenCvSharp.Size(640, 480),
                true);
            // reset the frameindex
            frameIndex = 0;

            if (!videoWriter.IsOpened())
            {
                MessageBox.Show("Failed to open video file for writing.");
                isStreaming = false;
            }
        }

        /// <summary>
        /// Stops and disposes the current video writer.
        /// </summary>
        private void StopRecording()
        {
            videoWriter?.Release();
            videoWriter?.Dispose();
            videoWriter = null;
            isStreaming = false;
        }

        /// <summary>
        /// Displays incoming video frames and optionally writes them to file.
        /// </summary>
        /// <param name="frame">The OpenCV frame received from camera</param>
        /// <param name="cameraOutputForm">Form where frame is displayed</param>
        private void OnFrameReady(Mat frame, CameraOutputForm cameraOutputForm)
        {
            if (InvokeRequired)
            {
                Invoke(new Action(() => OnFrameReady(frame, cameraOutputForm)));
                return;
            }
            // Clone frame so we can annotate it without affecting LSL data
            var annotatedFrame = frame.Clone();

            if (isStreaming && videoWriter is not null)
            {
                // 1. Draw the frame number on the video
                Cv2.PutText(
                    annotatedFrame,
                    $"Frame: {frameIndex}",
                    new OpenCvSharp.Point(10, 30),
                    HersheyFonts.HersheySimplex,
                    1.0,
                    Scalar.Green,
                    2
                );

                // 2. Write annotated frame to video
                videoWriter.Write(annotatedFrame);
                frameIndex++;
            }
            var bitmap = BitmapConverter.ToBitmap(annotatedFrame);
            cameraOutputForm.pictureBox1.Image?.Dispose();
            cameraOutputForm.pictureBox1.Image = bitmap;
            if (streamOutlet is not null) 
            foreach (var outlet in streamOutlet)
            {
                    // 3. Send the frameIndex to LSL
                    outlet.push_sample((int[])[frameIndex]);
            }
        }
    }
}
