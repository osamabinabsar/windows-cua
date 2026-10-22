# READ-ONLY: enumerates Explorer child window class names + geometry only.
# No clicks, no activation, no moving, no screenshots.
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumProc cb, IntPtr p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

function Get-CN($h) { $sb = New-Object System.Text.StringBuilder 256; [void][W]::GetClassName($h, $sb, 256); $sb.ToString() }
function Get-WT($h) { $sb = New-Object System.Text.StringBuilder 256; [void][W]::GetWindowTextW($h, $sb, 256); $sb.ToString() }
function Get-Rect($h) { $r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r); return "($($r.Left),$($r.Top))-($($r.Right),$($r.Bottom))" }

$explorerPids = (Get-Process explorer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

$topHandles = New-Object System.Collections.ArrayList
$cb = [W+EnumProc]{
  param($h, $p)
  $procId = 0; [void][W]::GetWindowThreadProcessId($h, [ref]$procId)
  if ($explorerPids -contains $procId) { [void]$topHandles.Add($h) }
  return $true
}
[void][W]::EnumWindows($cb, [IntPtr]::Zero)

foreach ($th in $topHandles) {
  $cls = Get-CN $th; $txt = Get-WT $th
  if ($cls -ne 'CabinetWClass' -and $cls -ne 'ExploreWClass' -and $cls -ne 'WorkerW') { continue }
  $visible = [W]::IsWindowVisible($th)
  Write-Output "=== TOP class=$cls vis=$visible rect=$(Get-Rect $th) text='$txt' hwnd=$th ==="
  $kids = New-Object System.Collections.ArrayList
  $cb2 = [W+EnumProc]{ param($h,$p) [void]$kids.Add($h); return $true }
  [void][W]::EnumChildWindows($th, $cb2, [IntPtr]::Zero)
  foreach ($k in $kids) {
    $kc = Get-CN $k
    if ($kc -match 'NamespaceTreeControl|SysTreeView32|SHELLDLL_DefView|DirectUIHWND|FileExplorer_FolderViewIsland|DesktopChildSiteBridge|Chrome_WidgetWin_0|MsCdcNetDialog|MOUIFlows') {
      $vis = [W]::IsWindowVisible($k)
      Write-Output ("   {0,-32} vis={1,-5} rect={2}" -f $kc, $vis, (Get-Rect $k))
    }
  }
}
Write-Output "=== done ==="
