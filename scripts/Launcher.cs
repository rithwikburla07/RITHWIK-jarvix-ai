using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
#if MAINTENANCE
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string interpreter = Path.Combine(local, "Programs", "Python", "Python312", "python.exe");
        if (!File.Exists(interpreter)) interpreter = Path.Combine(root, ".venv", "Scripts", "python.exe");
        string entry = Path.Combine(root, "scripts", "maintain.py");
        foreach (string value in args)
            if (value != "uninstall" && value != "--dry-run" && value != "--purge" && value != "--check")
                return 2;
#else
        string interpreter = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
        string entry = Path.Combine(root, "main.py");
        if (args.Length > 0 && !(args.Length == 1 && args[0] == "--check"))
            return 2;
#endif
        try
        {
            if (args.Length == 1 && args[0] == "--check")
                return File.Exists(entry) && File.Exists(interpreter) ? 0 : 1;
            if (!File.Exists(interpreter) || !File.Exists(entry))
                throw new InvalidOperationException("Install Jarvix first, or run main.py with the existing project virtual environment.");
            ProcessStartInfo start = new ProcessStartInfo(interpreter);
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.EnvironmentVariables.Remove("PYTHONPATH");
            start.EnvironmentVariables.Remove("PYTHONHOME");
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.Arguments = "-B \"" + entry + "\"";
#if MAINTENANCE
            start.Arguments += " " + String.Join(" ", args);
#else
            start.CreateNoWindow = true;
#endif
            using (Process process = Process.Start(start))
            {
                process.WaitForExit();
                return process.ExitCode;
            }
        }
        catch (Exception error)
        {
#if MAINTENANCE
            Console.Error.WriteLine(error.Message);
#else
            MessageBox.Show(error.Message, "Jarvix Personal AI", MessageBoxButtons.OK, MessageBoxIcon.Error);
#endif
            return 1;
        }
    }
}
