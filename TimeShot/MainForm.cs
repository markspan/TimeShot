using MaterialSkin.Controls;
using LSL;                      // labstreaminglayer
using OpenCvSharp;              // for videocapture and videowriter
using OpenCvSharp.Extensions;   // for bitmapconverter

namespace TimeShot
{
    public partial class MainForm : MaterialForm
    {

        readonly MaterialSkin.MaterialSkinManager materialSkinManager;

        private readonly string[]? cameraList = null;
        private readonly List<CameraInfo>? cameraControls = null;
        private string? dataPath = null;
        private List<StreamInfo> streamInfo = null;
        private List<StreamOutlet> streamOutlet = null;

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
        private void GetAvailableCameras()
        {
            // Check available cameras by their index
            for (int i = 0; i < 10; i++) // Check up to 10 camera indexes (you can adjust this)
            {
                using (var capture = new VideoCapture(i))
                {
                    if (capture.IsOpened())
                    {
                        CameraInfo cam = new CameraInfo();
                        cam.CamName.Text = $"Camera {i}";
                        cam.FileName.Text = $"cam{i}.mp4";
                        cam.StreamName.Text = $"cam{i}stream";
                        cam.Size = new System.Drawing.Size(569, 52);
                        cam.Location = new System.Drawing.Point(9, 3 + (54 * i));
                        CameraBox.Controls.Add(cam);
                    }
                }
            }
        }

        private void CreateStreamButton_Click(object sender, EventArgs e)
        {
            foreach (Control control in CameraBox.Controls)
            {
                streamInfo = new List<StreamInfo> { };
                streamOutlet = new List<StreamOutlet> { };

                if (control is CameraInfo cameraInstance)
                {
                    if (cameraInstance.Check.Checked)
                    {
                        // Create a stream for the camera
                        string streamName = cameraInstance.StreamName.Text;
                        string fileName = cameraInstance.FileName.Text;
                        // Implement the logic to create a stream using LSL or any other method
                        // For example:
                        var si = new StreamInfo(streamName, "Markers", 1, 0, channel_format_t.cf_int64, DateTime.Now.ToString());
                        streamInfo.Add(si);
                        streamOutlet.Add(new StreamOutlet(si));
                    }
                    CreateStreamButton.Enabled = false;
                }
            }
        }

        private void StopButton_Click(object sender, EventArgs e)
        {
            if (StreamButton.Enabled == false)
            {
                StreamButton.Enabled = true;
                return;
            }
            if (CreateStreamButton.Enabled == false)
            {
                CreateStreamButton.Enabled = true;
                foreach (var stream in streamInfo) stream.Close();
                foreach (var stream in streamOutlet) stream.Close();

                streamInfo.Clear();
                streamOutlet.Clear();

                return;
            }
        }

        private void StreamButton_Click(object sender, EventArgs e)
        {

        }
    }
}
