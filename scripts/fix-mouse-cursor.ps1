<#
.SYNOPSIS
    Diagnose / fix "the mouse cursor is invisible over some surfaces"
    on Windows 11 with NVIDIA Multi-Plane Overlay (MPO).

.DESCRIPTION
    THE VERDICT ON THIS MACHINE
    ---------------------------
    The pointer is not missing and Windows never hides it.  Measured here:

      * GetCursorInfo always reports CURSOR_SHOWING=1, CURSOR_SUPPRESSED=0
        and a real system arrow handle.
      * The arrow glyph is never composited into the screen image (dark 0/67
        and light 0/76 template pixels at the pointer, against 67/67 and
        0/76 for a control 64px away).  The pointer lives on the display
        adapter's HARDWARE CURSOR PLANE, not in the framebuffer.
      * The arrow is two-tone (67 dark outline pixels + 76 light body
        pixels), so no background can camouflage it.  It is COVERED, or
        the cursor plane is not being refreshed over that region.

    MPO IS A CANDIDATE, NOT A PROVEN MECHANISM
    ------------------------------------------
    GraphicsDrivers\FeatureSetUsage\AdapterMPO_3 = 1 says the ADAPTER
    supports MPO.  It does NOT say a given OUTPUT has an overlay plane.
    (Both the key and the value name are UNDOCUMENTED by Microsoft.)
    dxdiag reports MPO per output, and on this machine the two outputs
    differ:

        \\.\DISPLAY1 (PRIMARY, DisplayPort)  MPO MaxPlanes: 1
                                             MPO Caps: Not Supported
        \\.\DISPLAY2 (secondary, HDMI)       MPO MaxPlanes: 4
                                             MPO Caps: ... (MPO3)

    "MPO MaxPlanes" COUNTS THE PRIMARY SURFACE as one of the planes, so
    "MaxPlanes: 1" means the root surface only - ZERO overlay planes -
    and "MaxPlanes: 4" means the primary plus three overlay planes.
    (Microsoft: the maximum is "the maximum number of inputs, including
    the primary surface, to the display hardware that can be supported in
    the current mode".)

    So as currently reported, the PRIMARY display has no overlay plane,
    and no overlay plane can be scanned out above the cursor there.  (A
    promoted surface occupies ONE rectangle, so a plane cannot split ONE
    window into "cursor visible here, invisible there" - which is exactly
    how the symptom presents in Explorer and VS Code.  A promoted
    SUB-rectangle is not excluded, but nothing shows one in this case.)

    These readings are per output and MODE-DEPENDENT - a refresh-rate or
    mode change, a clone configuration, or unplugging the second display
    can change them.  Re-check if the display configuration changes.

    Disabling MPO is therefore worth trying for symptoms on the
    SECONDARY output, and is unlikely to help the primary-display ones.

    THE FIX LADDER: A TWO-STEP APPROACH
    -----------------------------------
    Step 1 (safe, this is what NVIDIA itself ships):

        HKLM\SOFTWARE\Microsoft\Windows\Dwm
            OverlayTestMode  (REG_DWORD) = 5

    Step 2 (aggressive, only if step 1 is not enough):

        HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers
            DisableOverlays  (REG_DWORD) = 1

    Both need a full power cycle to take effect (Shut down -> Shut down,
    not a restart - a restart keeps the kernel session alive).  Revert =
    delete the values and power cycle again.

    (NVIDIA's KB 5157 itself says only "reboot the PC"; the full power
    cycle is what the NVIDIA GeForce Forums thread that reports the
    CURSOR symptom specifically specifies, and it is the more reliable
    of the two.)

    NOTE: it is COMMUNITY-REPORTED (and DISPUTED) that on 25H2 the
    DWM-path OverlayTestMode may be ignored in favour of the
    GraphicsDrivers\DisableOverlays value.  The MPO-GPU-FIX maintainer
    calls that claim "misinformation" and reports OverlayTestMode still
    working on 25H2 and 26H1.  The distinction it points at is real
    though: the DWM-path keys only FORCE MPO ONTO A SINGLE PLANE, while
    DisableOverlays genuinely DISABLES OVERLAY SCHEDULING.  So if step
    1 appears to do nothing, go to step 2 rather than assuming it
    worked - and do not report step 2 as "the fix" either.

    NOTE ON OverlayMinFPS - SYMPTOM-DEPENDENT, NOT A BLANKET "NEVER"
    ------------------------------------------------------------------
    "OverlayMinFPS" is the ONE overlay value Microsoft actually documents
    (learn.microsoft.com/en-us/windows/win32/dwm/registry-values), under
    HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm:

        "A DWORD. If this value is present and set to zero, the Desktop
         Window Manager disables its minimum frame rate requirement for
         assigning DirectX swap chains to overlay planes in hardware that
         supports overlays.  This makes it more likely that a low frame
         rate swap chain will get assigned and stay assigned to an overlay
         plane, if available.  This mechanism was introduced in its
         present form in Windows 11.  This registry value may be modified
         or removed in future releases."

    The deciding word is "STAY".  The value does two things: it makes
    more surfaces ELIGIBLE for an overlay plane, and it makes an
    existing assignment STICK.  That gives it opposite meanings for
    two different symptoms:

      * the WINDOW CONTENT stutters - the surface flip-flops between
        Composed: Flip and Hardware Composed: Independent Flip, freezing
        or tearing for a frame (the 24H2/25H2 presentation-path bug in
        Chromium/Electron apps)
            -> "stay assigned" is the cure.  THIS IS THE RIGHT KNOB.
      * the POINTER is STEADILY GONE over a region (this symptom), while
        the content animates perfectly
            -> it makes more of the screen sit on an overlay plane for
               longer, i.e. it moves toward the suspect condition.
               THIS IS THE WRONG KNOB.

    Note that the pointer is the element that KEEPS UPDATING through the
    presentation-path flip-flop, which is why that bug was never a
    plausible explanation for a pointer that is steadily absent.

    Two path caveats.  Much community advice writes OverlayMinFPS
    under HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers
    instead of the Microsoft-documented Dwm key; reverse-engineering
    notes give a DEFAULT of 15.  Microsoft also warns the value "may be
    modified or removed in future releases", and it is COMMUNITY
    REPORTED (not Microsoft-acknowledged) to have stopped taking effect
    on 25H2, where DisableOverlays is reported to be required instead.

    Community advice that pairs OverlayMinFPS=0 with OverlayTestMode=5
    is advice aimed at the FIRST symptom, and the two are frequently
    conflated.  An earlier version of this script said "never set it";
    that was too strong.  It is REPORTED ONLY here: set it yourself only
    if you have confirmed the CONTENT-STUTTER variant.

    OverlayTestMode is UNDOCUMENTED by Microsoft - it is not on that page.
    It is nonetheless what NVIDIA's own mpo_disable.reg contains (and
    that file writes NO OverlayMinFPS value), and what WinUtil's
    "Disabled (Compatibility)" preset writes.  WinUtil's "Fully
    Disabled" preset writes BOTH OverlayTestMode = 5 and
    GraphicsDrivers\DisableOverlays = 1 - which is exactly what
    -Level Full does here.

    VERIFYING
    ---------
    After rebooting, run:  dxdiag  ->  Save All Information  ->  search
    for "MPO".

    IMPORTANT - do NOT check the primary display.  It reports
    "MPO MaxPlanes: 1" and "MPO Caps: Not Supported" whether or not MPO
    is enabled, because that output has no overlay planes at all.  The
    only output whose values can change is the SECONDARY (HDMI) one,
    which reads "MPO MaxPlanes: 4" while MPO is on.

    Read that number as a CAPABILITY report, not a usage counter.
    "MaxPlanes" is the driver-advertised number of planes that "can be
    supported in the current mode", while OverlayTestMode / OverlayMinFPS
    are DWM-side ASSIGNMENT switches.  So after the DWM-side
    OverlayTestMode = 5 dxdiag may still print 4; and MaxPlanes NOT
    falling is not evidence that the override failed - nor is it
    established that DisableOverlays = 1 changes the reported MaxPlanes
    at all.  The behaviour to check is whether DWM stops promoting swap
    chains to overlay planes (in PresentMon, "Hardware Composed:
    Independent Flip" should give way to "Composed: Flip"; NVIDIA's
    MPO_INDICATOR profile option shows the same thing) and whether the
    symptom itself moves.

.PARAMETER Action
    Status  - report the current MPO / cursor-plane state (read-only)
    Apply   - write the registry override (needs Administrator; needs a reboot)
    Revert  - remove every override and restore the Windows default

.PARAMETER Level
    Compatibility (default) - step 1 only: OverlayTestMode = 5.
    Full                    - steps 1 + 2: OverlayTestMode = 5 AND
                               DisableOverlays = 1.  Aggressive; can crash
                               DX12 games.  Try Compatibility first.

.EXAMPLE
    # If the machine's execution policy blocks the script, widen it for THIS
    # shell only (it is session-scoped and does not change the machine or
    # user policy), then call the script:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
    .\fix-mouse-cursor.ps1
    .\fix-mouse-cursor.ps1 -Action Apply
    .\fix-mouse-cursor.ps1 -Action Apply -Level Full
    .\fix-mouse-cursor.ps1 -Action Revert

    # The one-liner form, if you prefer it.  NOTE: -ExecutionPolicy Bypass
    # disables script-execution enforcement for that invocation, so use it
    # deliberately rather than reflexively - the Process-scope form above is
    # the narrower equivalent.
    powershell -ExecutionPolicy Bypass -File .\fix-mouse-cursor.ps1
#>

[CmdletBinding()]
param(
    [ValidateSet('Status', 'Apply', 'Revert')]
    [string]$Action = 'Status',

    [ValidateSet('Compatibility', 'Full')]
    [string]$Level = 'Compatibility'
)

$ErrorActionPreference = 'Stop'

$DwmKey          = 'HKLM:\SOFTWARE\Microsoft\Windows\Dwm'
$GraphicsDrivers = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'

# Registry values this script knows about, and which key each lives in.
$OverlayTestMode = 'OverlayTestMode'   # HKLM\SOFTWARE\Microsoft\Windows\Dwm
$DisableOverlays = 'DisableOverlays'   # HKLM\...\Control\GraphicsDrivers
$OverlayMinFPS   = 'OverlayMinFPS'     # HKLM\SOFTWARE\Microsoft\Windows\Dwm (reported only)

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DwmValue {
    param([string]$Key, [string]$Name)
    if (-not (Test-Path $Key)) { return $null }
    $p = Get-ItemProperty -Path $Key -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $p) { return $null }
    $v = $p.$Name
    if ($null -eq $v) { return $null }
    return [int]$v
}

function Set-DwmValue {
    param([string]$Key, [string]$Name, [int]$Value)
    if (-not (Test-Path $Key)) { New-Item -Path $Key -Force | Out-Null }
    New-ItemProperty -Path $Key -Name $Name -Value $Value `
        -PropertyType DWord -Force | Out-Null
}

function Remove-DwmValue {
    param([string]$Key, [string]$Name)
    if (Test-Path $Key) {
        Remove-ItemProperty -Path $Key -Name $Name -ErrorAction SilentlyContinue
    }
}

function Write-State {
    Write-Host ''
    Write-Host '=== Mouse cursor / MPO state ===' -ForegroundColor Cyan

    # --- the cursor itself -------------------------------------------------
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class CursorState {
    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    public struct CURSORINFO {
        public uint cbSize, flags;
        public IntPtr hCursor;
        public POINT ptScreenPos;
    }
    [DllImport("user32.dll")] public static extern bool GetCursorInfo(ref CURSORINFO ci);
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
    [DllImport("user32.dll")] public static extern bool SystemParametersInfo(
        uint uiAction, uint uiParam, ref int pvParam, uint fWinIni);
}
'@
    $ci = New-Object CursorState+CURSORINFO
    $ci.cbSize = 24
    if ([CursorState]::GetCursorInfo([ref]$ci)) {
        Write-Host ("  Cursor flags         : 0x{0:X8}  (SHOWING={1} SUPPRESSED={2})" -f `
            $ci.flags, [int]($ci.flags -band 1), [int](($ci.flags -shr 1) -band 1))
        Write-Host ("  Cursor handle        : 0x{0:X}" -f [int64]$ci.hCursor)
        if ($ci.hCursor -eq [IntPtr]::Zero -and
            ($ci.flags -band 1) -eq 0) {
            Write-Host '  -> MECHANISM C: the OS has NO cursor for the window under the'
            Write-Host '     pointer - the APPLICATION suppressed it (SetCursor(NULL) from'
            Write-Host '     a WM_SETCURSOR handler, or CSS cursor:none).  Nothing is'
            Write-Host '     covering the pointer; there is nothing to display, so no'
            Write-Host '     MPO / driver / compositing change here will help.'
            Write-Host '     (A NULL window-class cursor is NOT one of the causes: it'
            Write-Host '     only removes the system arrow fallback - it does not hide'
            Write-Host '     the pointer.)'
        }
        else {
            Write-Host '                        : SHOWING=1 means the OS has a cursor to draw'
            Write-Host '                          there and has NOT hidden it - so it is being'
            Write-Host '                          covered, or the hardware cursor plane is not'
            Write-Host '                          refreshed over that region.  (SUPPRESSED=1'
            Write-Host '                          means touch/pen input, NOT that the cursor is'
            Write-Host '                          hidden.)'
        }
    }
    # NOTE ON GetSystemMetrics INDICES
    #   13 = SM_CXCURSOR      - nominal cursor WIDTH in pixels (older headers
    #                            also spell index 13 as SM_CURSORS, the number
    #                            of hardware cursors; the two are not
    #                            distinguishable on this hardware).
    #   There is NO SM_MOUSETRAILS.  Mouse trails are read with
    #                            SPI_GETMOUSETRAILS, below.
    # Whether the pointer is on the hardware cursor plane is NOT settled by a
    # system metric - it is settled by the glyph measurement in
    # scripts/cursorframe.py (the arrow is not in the composited framebuffer).
    Write-Host ("  Cursor size          : {0} x {1} px" -f `
        [CursorState]::GetSystemMetrics(13), [CursorState]::GetSystemMetrics(14))
    # SPI_GETMOUSETRAILS = 0x005E.  pvParam is an int, NOT a BOOL:
    # 0 or 1 = trails disabled, >1 = enabled and the value is the number of
    # cursors drawn in the trail.
    $trails = 0
    [CursorState]::SystemParametersInfo(0x005E, 0, [ref]$trails, 0) | Out-Null
    Write-Host ("  Mouse trails         : {0}  (0 or 1 = OFF, >1 = ON)" -f $trails)

    # --- MPO ----------------------------------------------------------------
    Write-Host ''
    Write-Host '  --- MPO ---'

    $otm = Get-DwmValue -Key $DwmKey -Name $OverlayTestMode
    if ($null -eq $otm) {
        Write-Host '  OverlayTestMode      : not set  (Windows default)'
    }
    else {
        $meaning = switch ($otm) {
            5 { 'MPO DISABLED (the override this script writes)' }
            4 { 'MPO FORCED ON - note: 4 = "force success" for MPO support, it does' +
                 ' NOT mean surfaces get an overlay plane (UNDOCUMENTED value)' }
            default { "unrecognised value - the value is UNDOCUMENTED by Microsoft" }
        }
        Write-Host "  OverlayTestMode      : $otm  ($meaning)"
        Write-Host "                         [$DwmKey]"
    }

    $dis = Get-DwmValue -Key $GraphicsDrivers -Name $DisableOverlays
    if ($null -eq $dis) {
        Write-Host '  DisableOverlays      : not set  (Windows default)'
    }
    else {
        Write-Host "  DisableOverlays      : $dis  (1 = overlays fully disabled - AGGRESSIVE)"
        Write-Host "                         [$GraphicsDrivers]"
    }

    $minfps = Get-DwmValue -Key $DwmKey -Name $OverlayMinFPS
    if ($null -eq $minfps) {
        Write-Host '  OverlayMinFPS        : not set  (Windows default)'
    }
    else {
        Write-Host "  OverlayMinFPS        : $minfps   (REPORTED ONLY - symptom-dependent;"
        Write-Host '                          0 makes overlay assignment STICK.  Right knob for'
        Write-Host '                          a stuttering/tearing WINDOW CONTENT, wrong knob'
        Write-Host '                          for a pointer that is steadily gone.)'
    }

    $fsu = Join-Path $GraphicsDrivers 'FeatureSetUsage'
    if (Test-Path $fsu) {
        $props = Get-ItemProperty $fsu
        foreach ($n in $props.PSObject.Properties.Name) {
            if ($n -like 'AdapterMPO*') {
                Write-Host "  $n : $($props.$n)   (1 = the ADAPTER supports MPO -"
                Write-Host '                          this says nothing about a given OUTPUT.'
                Write-Host '                          Run scripts\diagnose_display_split.py to see'
                Write-Host '                          which display a window is on.)'
            }
        }
    }

    Write-Host ''
    Write-Host '  To confirm the override actually took effect after a reboot:'
    Write-Host '    dxdiag  ->  Save All Information  ->  search for "MPO"'
    Write-Host '    Read the SECONDARY (HDMI) output block, not the primary:'
    Write-Host '    the primary already reads "MaxPlanes: 1 / Not Supported"'
    Write-Host '    whether or not MPO is on, so it is a false positive.'
    Write-Host '    The secondary reads "MaxPlanes: 4" while MPO is on - that'
    Write-Host '    is a CAPABILITY report, so it MAY still read 4 after the'
    Write-Host '    DWM-side OverlayTestMode = 5.  Not falling is not failure;'
    Write-Host '    check PresentMon for the presentation path instead.'
    Write-Host ''
}

Write-State

switch ($Action) {
    'Apply' {
        if (-not (Test-Admin)) {
            Write-Error 'Apply needs an Administrator PowerShell. Right-click PowerShell -> Run as administrator.'
        }

        if ($Level -eq 'Compatibility') {
            Set-DwmValue -Key $DwmKey -Name $OverlayTestMode -Value 5
            Write-Host 'Written: OverlayTestMode = 5  (MPO disabled - NVIDIA compatibility method).' -ForegroundColor Green
            Write-Host "         [$DwmKey]" -ForegroundColor DarkGray
        }
        else {
            Set-DwmValue -Key $DwmKey -Name $OverlayTestMode -Value 5
            Set-DwmValue -Key $GraphicsDrivers -Name $DisableOverlays -Value 1
            Write-Host 'Written: OverlayTestMode = 5  (MPO disabled - compatibility method).' -ForegroundColor Green
            Write-Host "         [$DwmKey]" -ForegroundColor DarkGray
            Write-Host 'Written: DisableOverlays = 1  (overlays fully disabled - AGGRESSIVE).' -ForegroundColor Green
            Write-Host "         [$GraphicsDrivers]" -ForegroundColor DarkGray
            Write-Host 'This is the aggressive step. It can crash DX12 games.' -ForegroundColor Yellow
        }

        Write-Host ''
        Write-Host 'Shut down and power back on for it to take effect.' -ForegroundColor Green
        Write-Host '  Use Win+X -> Shut down -> Shut down (a full power cycle, not a' -ForegroundColor DarkGray
        Write-Host '  restart) - a restart keeps the kernel session alive and the' -ForegroundColor DarkGray
        Write-Host '  override can appear not to have worked.' -ForegroundColor DarkGray
        Write-Host 'Then verify with dxdiag and re-test the surfaces where the pointer vanished.' -ForegroundColor Green
        Write-Host 'To undo:  .\fix-mouse-cursor.ps1 -Action Revert' -ForegroundColor DarkGray
    }
    'Revert' {
        if (-not (Test-Admin)) {
            Write-Error 'Revert needs an Administrator PowerShell.'
        }
        Remove-DwmValue -Key $DwmKey -Name $OverlayTestMode
        Remove-DwmValue -Key $GraphicsDrivers -Name $DisableOverlays
        Remove-DwmValue -Key $DwmKey -Name $OverlayMinFPS
        Write-Host 'Removed OverlayTestMode, DisableOverlays and OverlayMinFPS.' -ForegroundColor Green
        Write-Host 'Windows default MPO behaviour restored.' -ForegroundColor Green
        Write-Host 'Shut down and power back on for it to take effect.' -ForegroundColor Green
        Write-Host 'Note: OverlayMinFPS was removed from the DWM key only.' -ForegroundColor DarkGray
        Write-Host '  Much community advice writes it under GraphicsDrivers instead -' -ForegroundColor DarkGray
        Write-Host '  if YOU set it there, delete it by hand; this script will not.' -ForegroundColor DarkGray
    }
}
