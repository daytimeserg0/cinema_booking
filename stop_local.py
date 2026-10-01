"""Stop only this project's running Flask process on Windows."""

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
pid_file = ROOT / ".local" / "app.pid"

if __name__ == "__main__":
    if not pid_file.exists():
        print("No running app recorded. Stop the app in PyCharm if it is open there.")
    else:
        app_pid = int(pid_file.read_text(encoding="ascii").strip())
        script = """
        $ErrorActionPreference = 'Stop'
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $env:CINEMA_APP_PID"
        if ($null -eq $taskProcess) { exit 0 }
        if ($taskProcess.Name -notin @('python.exe', 'pythonw.exe') -or
            $taskProcess.CommandLine.IndexOf($env:CINEMA_APP_SCRIPT, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw 'Recorded PID is not this project. Stop the app from its terminal or PyCharm.'
        }
        Stop-Process -Id ([int]$env:CINEMA_APP_PID) -ErrorAction Stop
        """
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=dict(os.environ, CINEMA_APP_PID=str(app_pid), CINEMA_APP_SCRIPT=str(ROOT / "run_local.py")),
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
        )
        pid_file.unlink(missing_ok=True)
        print("App stopped. PostgreSQL remains running.")
