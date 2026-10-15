// GPL-3.0. Ordinary .NET launcher; application and runtime remain loose files.
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
[assembly:AssemblyTitle("Texture Converter")]
[assembly:AssemblyProduct("Texture Converter ALPHA 1.0")]
[assembly:AssemblyCompany("private0516")]
[assembly:AssemblyVersion("1.0.0.0")]
[assembly:AssemblyInformationalVersion("ALPHA 1.0")]
static class TextureLauncher {
    static string Quote(string s) {
        StringBuilder b=new StringBuilder("\""); int n=0;
        foreach(char c in s) {
            if(c=='\\') { n++; continue; }
            b.Append('\\',c=='"'?n*2+1:n); b.Append(c); n=0;
        }
        b.Append('\\',n*2); return b.Append('"').ToString();
    }
    [STAThread] static int Main(string[] args) {
        string log=null;
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            string local=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"private0516","TextureConverter");
            Directory.CreateDirectory(local);
            log=Path.Combine(local,"launcher-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fff")+".log");
            string script=Path.Combine(root,"app",args.Length==0?"main.py":"texture_cli.py");
            string python=Path.Combine(root,"runtime","python.exe");
            if(!File.Exists(script)||!File.Exists(python))throw new FileNotFoundException("Keep TextureConverter.exe together with app, runtime and vendor folders.");
            StringBuilder command=new StringBuilder("-B -X utf8 "+Quote(script));
            int startAt=args.Length>0&&args[0]=="--cli"?1:0;
            for(int i=startAt;i<args.Length;i++)command.Append(" ").Append(Quote(args[i]));
            ProcessStartInfo start=new ProcessStartInfo(python,command.ToString());
            start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;
            start.WindowStyle=ProcessWindowStyle.Hidden;start.RedirectStandardOutput=true;start.RedirectStandardError=true;
            start.StandardOutputEncoding=Encoding.UTF8;start.StandardErrorEncoding=Encoding.UTF8;
            // Persist JSON/masks in the user's profile, not in read-only install folders.
            start.EnvironmentVariables["TEXTURE_CONVERTER_CONFIG"]=Path.Combine(local,"config.json");
            using(Process p=new Process()) {
                p.StartInfo=start;object sync=new object();
                DataReceivedEventHandler append=delegate(object sender,DataReceivedEventArgs e) {if(e.Data!=null)lock(sync){try{File.AppendAllText(log,e.Data+Environment.NewLine,Encoding.UTF8);}catch(IOException){}}};
                p.OutputDataReceived+=append;p.ErrorDataReceived+=append;p.Start();p.BeginOutputReadLine();p.BeginErrorReadLine();p.WaitForExit();
                if(p.ExitCode!=0&&args.Length==0)MessageBox.Show("Texture Converter could not start.\nSee log:\n"+log,"Texture Converter",MessageBoxButtons.OK,MessageBoxIcon.Error);
                return p.ExitCode;
            }
        }catch(Exception e){if(args.Length==0)MessageBox.Show(e.Message+"\n"+log,"Texture Converter");return 1;}
    }
}
