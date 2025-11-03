using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Data;
using System.Drawing;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace TimeShot
{
    public partial class CameraOutputForm : Form
    {
        public CameraOutputForm()
        {
            InitializeComponent();
        }

        private void CameraOutputForm_MouseDoubleClick(object sender, MouseEventArgs e)
        {

        }

        private void CameraOutputForm_DoubleClick(object sender, EventArgs e)
        {

        }

        private void CameraOutputForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            // Prevent the user from closing the form
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                this.WindowState = FormWindowState.Minimized; // just minimize instead
            }
        }
    }
}
