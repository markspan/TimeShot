using OpenCvSharp;
using System.Collections.Concurrent;
using System.Diagnostics;

namespace TimeShot
{
    /// <summary>
    /// Writes recorded frames to the video file on a thread of its own, so drawing the frame
    /// number, encoding and disk I/O never delay the capture loop, and with it the moment the
    /// next frame's LSL marker is pushed (and stamped). Frames are written strictly in the order
    /// they were added and never dropped: frame N of the file must stay the frame of marker N.
    /// </summary>
    public sealed class FrameWriter
    {
        // How many seconds of frames may wait to be written before Add() makes the capture loop
        // wait. That absorbs a slow disk flush or a busy CPU without losing a frame, while
        // bounding memory (about 370 MB for 1080p at 30 fps); waiting delays the next markers
        // but keeps every frame, where dropping one would shift all later frames off their markers.
        private const double MaxBacklogSeconds = 2;

        private readonly VideoWriter writer;
        private readonly string fileName;
        private readonly BlockingCollection<(int Index, Mat Frame)> queue;
        private readonly Task completion;
        private int written; // writer thread only

        private FrameWriter(VideoWriter writer, string fileName, double fps)
        {
            this.writer = writer;
            this.fileName = fileName;
            queue = new BlockingCollection<(int, Mat)>(Math.Max(1, (int)(fps * MaxBacklogSeconds)));
            // LongRunning: the loop blocks for the whole recording, so it gets a thread of its
            // own instead of holding on to a thread pool thread.
            completion = Task.Factory.StartNew(WriteLoop, CancellationToken.None,
                TaskCreationOptions.LongRunning, TaskScheduler.Default);
        }

        /// <summary>
        /// Creates (or overwrites) the video file and starts the writer thread.
        /// </summary>
        /// <returns>The writer, or null if the file could not be created.</returns>
        public static FrameWriter? Create(string fileName, double fps, OpenCvSharp.Size frameSize)
        {
            // MJPG, not H264: OpenCV's H264 writer needs the separate OpenH264 DLL next to the
            // exe and just silently fails to open without it (see the IsOpened() check below).
            // MJPG ships with OpenCV's own FFmpeg backend everywhere, and -- more importantly
            // for this tool -- every frame is independently encoded (no GOPs/B-frames), so
            // seeking to exactly "frame N" (the join key the LSL marker stream uses) is exact.
            //
            // Real captured resolution/fps, not a hardcoded guess: a writer opened at the
            // wrong size relative to what the camera actually delivers corrupts frames.
            var writer = new VideoWriter(fileName, FourCC.MJPG, fps, frameSize, true);
            if (writer.IsOpened())
                return new FrameWriter(writer, fileName, fps);
            writer.Dispose();
            return null;
        }

        /// <summary>Completes once every added frame is written and the file is finalised.</summary>
        public Task Completion => completion;

        /// <summary>
        /// Burns the frame number into the top left corner of <paramref name="image"/>: the same
        /// label in the recording and in the preview.
        /// </summary>
        public static void DrawFrameNumber(Mat image, int index) =>
            Cv2.PutText(image, $"{index}", new OpenCvSharp.Point(10, 30), HersheyFonts.HersheySimplex, .75, Scalar.Red, 2);

        /// <summary>
        /// Queues a copy of <paramref name="frame"/> as frame <paramref name="index"/>; the caller
        /// may reuse its Mat straight away. Waits only while the writer is
        /// <see cref="MaxBacklogSeconds"/> behind.
        /// </summary>
        public void Add(int index, Mat frame)
        {
            var item = (index, frame.Clone());
            if (queue.TryAdd(item))
                return;

            // The encoder or the disk cannot keep up. Until there is room the capture loop waits,
            // so the frames that follow are stamped late (MovieMerger's "streams" command shows
            // that as marker bursts).
            Debug.WriteLine($"FrameWriter: {fileName} is {queue.BoundedCapacity} frames behind; capture waits for it.");
            queue.Add(item);
        }

        /// <summary>
        /// Announces that no frames follow: the writer writes what is queued, then finalises the
        /// file (index and duration). Call it from the thread that adds frames, after its last Add.
        /// </summary>
        public void Complete() => queue.CompleteAdding();

        private void WriteLoop()
        {
            try
            {
                foreach (var (index, frame) in queue.GetConsumingEnumerable())
                {
                    using (frame)
                    {
                        // Keep consuming even if one frame fails, so Add() never waits for good.
                        try
                        {
                            if (index != written)
                                Debug.WriteLine($"FrameWriter: {fileName} is out of step with its markers " +
                                    $"(marker {index} at file frame {written}).");
                            DrawFrameNumber(frame, index);
                            writer.Write(frame);
                            written++;
                        }
                        catch (Exception ex)
                        {
                            Debug.WriteLine($"FrameWriter: writing frame {index} to {fileName} failed: {ex.Message}");
                        }
                    }
                }
            }
            finally
            {
                writer.Release();
            }
        }
    }
}
