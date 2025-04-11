namespace TimeShot
{
    partial class MainForm
    {
        /// <summary>
        ///  Required designer variable.
        /// </summary>
        private System.ComponentModel.IContainer components = null;

        /// <summary>
        ///  Clean up any resources being used.
        /// </summary>
        /// <param name="disposing">true if managed resources should be disposed; otherwise, false.</param>
        protected override void Dispose(bool disposing)
        {
            if (disposing && (components != null))
            {
                components.Dispose();
            }
            base.Dispose(disposing);
        }

        #region Windows Form Designer generated code

        /// <summary>
        ///  Required method for Designer support - do not modify
        ///  the contents of this method with the code editor.
        /// </summary>
        private void InitializeComponent()
        {
            CamerasLabel = new MaterialSkin.Controls.MaterialLabel();
            NameLabel = new MaterialSkin.Controls.MaterialLabel();
            FileLabel = new MaterialSkin.Controls.MaterialLabel();
            StreamLabel = new MaterialSkin.Controls.MaterialLabel();
            CameraBox = new MaterialSkin.Controls.MaterialCard();
            CreateStreamButton = new MaterialSkin.Controls.MaterialButton();
            StreamButton = new MaterialSkin.Controls.MaterialButton();
            StopButton = new MaterialSkin.Controls.MaterialButton();
            SuspendLayout();
            // 
            // CamerasLabel
            // 
            CamerasLabel.AutoSize = true;
            CamerasLabel.Depth = 0;
            CamerasLabel.Font = new Font("Roboto", 14F, FontStyle.Regular, GraphicsUnit.Pixel);
            CamerasLabel.Location = new Point(6, 75);
            CamerasLabel.MouseState = MaterialSkin.MouseState.HOVER;
            CamerasLabel.Name = "CamerasLabel";
            CamerasLabel.Size = new Size(64, 19);
            CamerasLabel.TabIndex = 0;
            CamerasLabel.Text = "Cameras";
            // 
            // NameLabel
            // 
            NameLabel.AutoSize = true;
            NameLabel.Depth = 0;
            NameLabel.Font = new Font("Roboto", 14F, FontStyle.Regular, GraphicsUnit.Pixel);
            NameLabel.Location = new Point(110, 75);
            NameLabel.MouseState = MaterialSkin.MouseState.HOVER;
            NameLabel.Name = "NameLabel";
            NameLabel.Size = new Size(43, 19);
            NameLabel.TabIndex = 2;
            NameLabel.Text = "Name";
            // 
            // FileLabel
            // 
            FileLabel.AutoSize = true;
            FileLabel.Depth = 0;
            FileLabel.Font = new Font("Roboto", 14F, FontStyle.Regular, GraphicsUnit.Pixel);
            FileLabel.Location = new Point(256, 75);
            FileLabel.MouseState = MaterialSkin.MouseState.HOVER;
            FileLabel.Name = "FileLabel";
            FileLabel.Size = new Size(26, 19);
            FileLabel.TabIndex = 3;
            FileLabel.Text = "File";
            // 
            // StreamLabel
            // 
            StreamLabel.AutoSize = true;
            StreamLabel.Depth = 0;
            StreamLabel.Font = new Font("Roboto", 14F, FontStyle.Regular, GraphicsUnit.Pixel);
            StreamLabel.Location = new Point(425, 75);
            StreamLabel.MouseState = MaterialSkin.MouseState.HOVER;
            StreamLabel.Name = "StreamLabel";
            StreamLabel.Size = new Size(52, 19);
            StreamLabel.TabIndex = 4;
            StreamLabel.Text = "Stream";
            // 
            // CameraBox
            // 
            CameraBox.BackColor = Color.FromArgb(255, 255, 255);
            CameraBox.Depth = 0;
            CameraBox.ForeColor = Color.FromArgb(222, 0, 0, 0);
            CameraBox.Location = new Point(6, 99);
            CameraBox.Margin = new Padding(14);
            CameraBox.MouseState = MaterialSkin.MouseState.HOVER;
            CameraBox.Name = "CameraBox";
            CameraBox.Padding = new Padding(14);
            CameraBox.Size = new Size(586, 204);
            CameraBox.TabIndex = 5;
            // 
            // CreateStreamButton
            // 
            CreateStreamButton.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            CreateStreamButton.Density = MaterialSkin.Controls.MaterialButton.MaterialButtonDensity.Default;
            CreateStreamButton.Depth = 0;
            CreateStreamButton.HighEmphasis = true;
            CreateStreamButton.Icon = null;
            CreateStreamButton.Location = new Point(7, 315);
            CreateStreamButton.Margin = new Padding(4, 6, 4, 6);
            CreateStreamButton.MouseState = MaterialSkin.MouseState.HOVER;
            CreateStreamButton.Name = "CreateStreamButton";
            CreateStreamButton.NoAccentTextColor = Color.Empty;
            CreateStreamButton.Size = new Size(146, 36);
            CreateStreamButton.TabIndex = 6;
            CreateStreamButton.Text = "Create Streams";
            CreateStreamButton.Type = MaterialSkin.Controls.MaterialButton.MaterialButtonType.Contained;
            CreateStreamButton.UseAccentColor = false;
            CreateStreamButton.UseVisualStyleBackColor = true;
            CreateStreamButton.Click += CreateStreamButton_Click;
            // 
            // StreamButton
            // 
            StreamButton.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            StreamButton.Density = MaterialSkin.Controls.MaterialButton.MaterialButtonDensity.Default;
            StreamButton.Depth = 0;
            StreamButton.HighEmphasis = true;
            StreamButton.Icon = null;
            StreamButton.Location = new Point(161, 315);
            StreamButton.Margin = new Padding(4, 6, 4, 6);
            StreamButton.MouseState = MaterialSkin.MouseState.HOVER;
            StreamButton.Name = "StreamButton";
            StreamButton.NoAccentTextColor = Color.Empty;
            StreamButton.Size = new Size(122, 36);
            StreamButton.TabIndex = 7;
            StreamButton.Text = "Stream/Save";
            StreamButton.Type = MaterialSkin.Controls.MaterialButton.MaterialButtonType.Contained;
            StreamButton.UseAccentColor = false;
            StreamButton.UseVisualStyleBackColor = true;
            StreamButton.Click += StreamButton_Click;
            // 
            // StopButton
            // 
            StopButton.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            StopButton.Density = MaterialSkin.Controls.MaterialButton.MaterialButtonDensity.Default;
            StopButton.Depth = 0;
            StopButton.HighEmphasis = true;
            StopButton.Icon = null;
            StopButton.Location = new Point(434, 315);
            StopButton.Margin = new Padding(4, 6, 4, 6);
            StopButton.MouseState = MaterialSkin.MouseState.HOVER;
            StopButton.Name = "StopButton";
            StopButton.NoAccentTextColor = Color.Empty;
            StopButton.Size = new Size(121, 36);
            StopButton.TabIndex = 8;
            StopButton.Text = "Stop/Cancel";
            StopButton.Type = MaterialSkin.Controls.MaterialButton.MaterialButtonType.Contained;
            StopButton.UseAccentColor = false;
            StopButton.UseVisualStyleBackColor = true;
            StopButton.Click += StopButton_Click;
            // 
            // MainForm
            // 
            AutoScaleDimensions = new SizeF(7F, 15F);
            AutoScaleMode = AutoScaleMode.Font;
            ClientSize = new Size(601, 360);
            Controls.Add(StopButton);
            Controls.Add(StreamButton);
            Controls.Add(CreateStreamButton);
            Controls.Add(CameraBox);
            Controls.Add(StreamLabel);
            Controls.Add(FileLabel);
            Controls.Add(NameLabel);
            Controls.Add(CamerasLabel);
            Name = "MainForm";
            Text = "TimeShot";
            ResumeLayout(false);
            PerformLayout();
        }

        #endregion

        private MaterialSkin.Controls.MaterialLabel CamerasLabel;
        private MaterialSkin.Controls.MaterialLabel NameLabel;
        private MaterialSkin.Controls.MaterialLabel FileLabel;
        private MaterialSkin.Controls.MaterialLabel StreamLabel;
        private MaterialSkin.Controls.MaterialCard CameraBox;
        private MaterialSkin.Controls.MaterialButton CreateStreamButton;
        private MaterialSkin.Controls.MaterialButton StreamButton;
        private MaterialSkin.Controls.MaterialButton StopButton;
    }
}
