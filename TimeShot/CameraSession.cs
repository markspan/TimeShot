using System.Windows.Forms;

namespace TimeShot
{
    /// <summary>
    /// Pairs a <see cref="CameraFrameStreamer"/> with its preview form for one camera.
    /// </summary>
    public class CameraSession
    {
        private readonly CameraFrameStreamer frameStreamer;
        public CameraOutputForm OutputForm => frameStreamer.OutputForm;

        public CameraSession(int index, string cameraName, string file, string stream)
        {
            frameStreamer = new CameraFrameStreamer(index, cameraName, file, stream);
        }

        /// <summary>
        /// Start recording. Awaits (without blocking the UI thread) if <paramref name="cs"/> is
        /// checked and no LSL consumer is connected to the marker stream yet.
        /// </summary>
        public Task Start(CheckState cs) => frameStreamer.StartRecording(cs);

        /// <summary>
        /// Stop recording and release all resources; awaits the capture loop's actual shutdown.
        /// </summary>
        public Task Stop() => frameStreamer.Stop();

        public bool IsRecording => frameStreamer.IsRecording;
    }
}
