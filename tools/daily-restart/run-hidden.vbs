' Launch the daily restart script with no console window (Task Scheduler action).
' Keep this file ASCII-only: WSH reads .vbs as ANSI, so non-ASCII text would break.
Option Explicit

Dim fso, shell, scriptDir, ps1, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = scriptDir & "\restart-server.ps1"

If Not fso.FileExists(ps1) Then
    WScript.Quit 1
End If

' 0 = hidden window, False = do not wait for the script to finish.
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """ -NoBrowser"
shell.Run cmd, 0, False
