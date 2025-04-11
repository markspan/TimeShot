namespace TimeShot
{
    partial class CameraInfo
    {
        /// <summary> 
        /// Required designer variable.
        /// </summary>
        private System.ComponentModel.IContainer components = null;

        /// <summary> 
        /// Clean up any resources being used.
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

        #region Component Designer generated code

        /// <summary> 
        /// Required method for Designer support - do not modify 
        /// the contents of this method with the code editor.
        /// </summary>
        private void InitializeComponent()
        {
            Check = new MaterialSkin.Controls.MaterialCheckbox();
            CamName = new MaterialSkin.Controls.MaterialTextBox();
            FileName = new MaterialSkin.Controls.MaterialTextBox();
            StreamName = new MaterialSkin.Controls.MaterialTextBox();
            SuspendLayout();
            // 
            // Check
            // 
            Check.AutoSize = true;
            Check.Depth = 0;
            Check.Location = new Point(0, 6);
            Check.Margin = new Padding(0);
            Check.MouseLocation = new Point(-1, -1);
            Check.MouseState = MaterialSkin.MouseState.HOVER;
            Check.Name = "Check";
            Check.ReadOnly = false;
            Check.Ripple = true;
            Check.Size = new Size(90, 37);
            Check.TabIndex = 0;
            Check.Text = "Camera";
            Check.UseVisualStyleBackColor = true;
            // 
            // CamName
            // 
            CamName.AnimateReadOnly = false;
            CamName.BorderStyle = BorderStyle.None;
            CamName.Depth = 0;
            CamName.Font = new Font("Roboto", 16F, FontStyle.Regular, GraphicsUnit.Pixel);
            CamName.LeadingIcon = null;
            CamName.Location = new Point(93, 0);
            CamName.MaxLength = 50;
            CamName.MouseState = MaterialSkin.MouseState.OUT;
            CamName.Multiline = false;
            CamName.Name = "CamName";
            CamName.Size = new Size(141, 50);
            CamName.TabIndex = 1;
            CamName.Text = "";
            CamName.TrailingIcon = null;
            // 
            // FileName
            // 
            FileName.AnimateReadOnly = false;
            FileName.BorderStyle = BorderStyle.None;
            FileName.Depth = 0;
            FileName.Font = new Font("Roboto", 16F, FontStyle.Regular, GraphicsUnit.Pixel);
            FileName.LeadingIcon = null;
            FileName.Location = new Point(240, 0);
            FileName.MaxLength = 50;
            FileName.MouseState = MaterialSkin.MouseState.OUT;
            FileName.Multiline = false;
            FileName.Name = "FileName";
            FileName.Size = new Size(163, 50);
            FileName.TabIndex = 2;
            FileName.Text = "";
            FileName.TrailingIcon = null;
            // 
            // StreamName
            // 
            StreamName.AnimateReadOnly = false;
            StreamName.BorderStyle = BorderStyle.None;
            StreamName.Depth = 0;
            StreamName.Font = new Font("Roboto", 16F, FontStyle.Regular, GraphicsUnit.Pixel);
            StreamName.LeadingIcon = null;
            StreamName.Location = new Point(409, 0);
            StreamName.MaxLength = 50;
            StreamName.MouseState = MaterialSkin.MouseState.OUT;
            StreamName.Multiline = false;
            StreamName.Name = "StreamName";
            StreamName.Size = new Size(149, 50);
            StreamName.TabIndex = 3;
            StreamName.Text = "";
            StreamName.TrailingIcon = null;
            // 
            // CameraInfo
            // 
            AutoScaleDimensions = new SizeF(7F, 15F);
            AutoScaleMode = AutoScaleMode.Font;
            Controls.Add(StreamName);
            Controls.Add(FileName);
            Controls.Add(CamName);
            Controls.Add(Check);
            Name = "CameraInfo";
            Size = new Size(567, 53);
            ResumeLayout(false);
            PerformLayout();
        }

        #endregion

        public MaterialSkin.Controls.MaterialCheckbox Check;
        public MaterialSkin.Controls.MaterialTextBox CamName;
        public MaterialSkin.Controls.MaterialTextBox FileName;
        public MaterialSkin.Controls.MaterialTextBox StreamName;
    }
}
