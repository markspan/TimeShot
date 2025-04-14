using OpenCvSharp;
using System;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace TimeShot
{
    public class CameraFrameStreamer
    {

        private VideoCapture? capture;
        private CancellationTokenSource? cancellationTokenSource;
        private VideoWriter? videoWriter;
        private bool isRecording = false;

        public event Action<Mat>? FrameReady;

        public void Start(int cameraIndex = 0)
        {
            capture = new VideoCapture(cameraIndex);
            if (!capture.IsOpened())
                throw new Exception("Cannot open webcam.");

            cancellationTokenSource = new CancellationTokenSource();
            Task.Run(() => CaptureLoop(cancellationTokenSource.Token));
        }

        public void Stop()
        {
            cancellationTokenSource?.Cancel();
            if (capture != null && !capture.IsDisposed)
            {
                capture?.Release();
                capture?.Dispose();
            }
        }

        private void CaptureLoop(CancellationToken token)
        {
            using var frame = new Mat();
            while (!token.IsCancellationRequested)
            {
                if (capture == null || !capture.IsOpened())
                    break;
                if (capture.Read(frame) && !frame.Empty())
                {
                    FrameReady?.Invoke(frame.Clone()); // Clone to avoid threading issues
                }
            }
        }
    }
}
