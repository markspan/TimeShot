namespace TimeShot
{
    /// <summary>
    /// Represents a configuration file. The {s} placeholder is the session/subject id;
    /// the {i} placeholder is replaced with the camera index.
    /// </summary>
    public class AppConfig
    {
        public string CameraName { get; set; } = "Camera {i}";
        public string StreamName { get; set; } = "Stream_{i}";
        public string VideoFile { get; set; } = "Video_{i}.avi";
    }
}
