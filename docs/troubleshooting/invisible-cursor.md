# Invisible mouse cursor over some Windows surfaces

**Status:** diagnosed 2026-10-22 on this machine.
**Applies to:** Windows 11 26H2 (build 26300.9711, KB5127648), NVIDIA RTX 2060, driver 32.0.15.7602 / 576.02, two monitors at mixed scaling (100 % and 125 %).

---

## The symptom

The pointer disappears over *some* UI surfaces but not others, in the
same application and the same window:

| Surface | Pointer | Display | Cursor-state probe (`GetCursorInfo`) |
|---|---|---|---|
| Windows Explorer — navigation pane / quick access | **invisible** | primary (Home window) | **measured** — a **real, showing** cursor over the nav-pane tree (the pointing hand, `0x1001F`), so **not** mechanism C: a **display-path** fault (§3, §3b) |
| Windows Explorer — file list (e.g. Downloads) | visible | primary | **measured** — a real, shown cursor: `hCursor = 0x10003` (`IDC_ARROW`), `CURSOR_SHOWING` set — the visible control (§3b) |
| Windows Search — results list | **invisible** | **not recorded** | **not measured** |
| VS Code — file explorer tree | **invisible** | primary | **not measured** — one sample over a VS Code render widget reported a *live* cursor, at a position not recorded |
| Chat app — a sent message | **invisible** | secondary | **not measured** — one sample elsewhere in the app reported a live I-beam (**unattributed**, see "What is still open") |
| Windows Terminal, plain desktop | visible | — | live cursor (401-sample run) |
| *(not a reported surface)* Brave render widget, (901, 660) | *visibility not recorded* | primary | **`hCursor = 0x0`, `CURSOR_SHOWING` clear** — mechanism C, 2956 samples |

**Of the four surfaces the user reported, one has now been probed at
the exact spot where the pointer is missing** — the Explorer navigation
pane, which reported a **real, showing** cursor (so not mechanism C) —
and the other three have not. "Which mechanism applies to which
surface" is therefore still open for the Windows Search results list,
the VS Code file-explorer tree and the chat surface, and the cheapest
next measurement is a per-surface cursor-state read — one command,
described in [the fixes section](#0-the-by-eye-tests--do-these-first-they-cost-nothing-and-they-decide-the-mechanism).

The hovered item still highlights, so the app is receiving the mouse.

## The verdict

**It is a Windows 11 / display-driver compositing fault — or an
application suppressing the cursor for a region. Not a setting, and not
the MCP tooling.** Three things are established by measurement, and the
first one **corrects an earlier claim in this document**:

1. **Windows never hides the pointer — but the *application* can, and
   on at least one surface it does.** This was previously stated here as
   "no app called `SetCursor(NULL)` in any of 8296 live samples", and
   that claim was **wrong**: it was an artefact of where the pointer
   happened to be. A targeted probe over a Chromium render widget
   (Brave, pid 18400) at (901, 660) returns

   ```
   hCursor = 0x0            (NULL)
   flags   = 0x00000000     (CURSOR_SHOWING clear, CURSOR_SUPPRESSED clear)
   ```

   stably, across **2956 samples over 90 seconds** — at **(901, 660)**,
   which by the display bounds in the table below is on the **primary**
   (DisplayPort) display. The OS has **no cursor at all** for that
   window — the application has suppressed it. Measured with
   `scripts/cursor_shapes.py`.

   A **two-hour census** (3919 samples, 22 distinct window chains)
   confirms the pattern and puts it in context: inside that one Brave
   render widget, **48 samples were mechanism C** at six distinct
   positions, against **1259 samples of a custom cursor** and **1161 of
   a live ARROW** at other positions of the *same* widget. (The "custom
   cursor" label is not reliable — it was produced with a broken
   `IDC_*` table in which a link hand could not be recognised; the
   `MECHANISM C` rows are read from the *flags* and are unaffected.
   See §1b.) Every other
   app in that run — the terminal, the taskbar, the Explorer window,
   Hermes, genspark — reported a real cursor handle, i.e. **`not C`**.
   Same app, same window, different region: the suppression is
   **per-region**, and it is the app's doing.
2. **The pointer is on the display adapter's hardware cursor plane**, not
   in the DWM framebuffer — the arrow glyph is never composited into the
   screen image, and it is two-tone (67 dark-class + 76 opaque light
   pixels), so no background can camouflage it. This is measured, not
   assumed: `scripts/cursor_is_composited.py` matches the cursor's own
   bitmaps against the screen at the pointer and then at the same crop
   **shifted by 1, 2, 4 and 8 pixels**; the *light* half of the glyph —
   the 76 fully opaque body pixels — matches at **0.05–0.07 at every
   offset** (0.06 at the exact position), where a genuinely composited
   arrow scores 1.00. (The
   *dark* half is the anti-aliased outline plus a soft drop shadow and
   is 2-3 px wide, so it does **not** collapse at a two-pixel shift and
   must not be used as the discriminator — see §2.) The pointer's pixels
   are simply not in the image (§2).
3. **The pointer is not camouflaged and not hidden by Windows**; over
   the affected regions it is either **suppressed by the app** or **not
   being displayed by the display path**. Those are the two families
   below, and they need different fixes.

| | Mechanism | Status |
|---|---|---|
| **A** | A **multi-plane overlay** plane is scanned out *above* the hardware cursor plane and hides it. | **Capability-excluded on the primary** — that output reports **zero** overlay planes (`MaxPlanes=1`, caps `0x00000A00`), `IDXGIOutput2::SupportsOverlays` is **FALSE on both outputs**, and the documented overlay z-order contains **no cursor layer** at all. On the secondary it is a *whole-window* hypothesis only — see below. |
| **B** | The pointer *is* on the hardware cursor plane, but the plane is **not refreshed / not scanned out** over the affected region (a driver or DWM compositing failure for accelerated content). | **Open** — the best fit for the primary display *among the display-path mechanisms*. C is still open there too. |
| **C** | The **application** suppresses the cursor over that region itself — `SetCursor(NULL)` from a `WM_SETCURSOR` handler, or CSS `cursor: none` in a Chromium/Electron renderer (which is the same act at the OS level: Chromium's `WinCursorFactory` hands the shell a **NULL** `HCURSOR` for `CursorType::kNone`, and `WinWindow::SetCursor` then calls `::SetCursor(nullptr)`). | **CONFIRMED on a Chromium surface, on the primary display** (measured above; the position (901, 660) is inside the primary's bounds), and the only one of the three that naturally explains an *intra-window* split. **Excluded over the Explorer navigation pane** — see below. |

> **The measurement that separates C from A and B — and it is *not*
> by-eye.** An earlier version of this document concluded that "no screen
> read can help, because the pointer's pixels are not in the framebuffer
> either way", and that only your eyes could separate the three
> mechanisms. **That was too strong.** The *framebuffer* route is blind,
> but the *cursor-state* route is not, and it is one API call:
>
> | `GetCursorInfo` says | What it means |
> |---|---|
> | `flags == 0x00000000` (`CURSOR_SHOWING` clear) | The OS is **not drawing** a cursor for the window under the pointer. The application suppressed it — `SetCursor(NULL)`, or CSS `cursor: none`. **Mechanism C. Decided.** (A NULL `hCursor` corroborates it, but key the test on the **flag**: `CURSORINFO.hCursor` is documented only as "a handle to the cursor", with no guarantee that it is NULL when the cursor is hidden.) |
> | `CURSOR_SHOWING` set, a real cursor handle | The OS has a cursor to draw and the app has not suppressed it. **C is excluded** for that surface; the fault is in the display path (A or B) — and *only* A-vs-B needs your eyes. (On the **primary**, **A** is capability-excluded (§3), so a primary-display surface narrows to **B** on its own.) |
> | `CURSOR_SUPPRESSED` (0x2) set | The pointer is being driven by **touch or pen** input. This flag says **nothing** about hiding, and should never appear in this table. |
>
> **One refinement to the middle row, so the table is not over-read.**
> "A real cursor handle ⇒ C is excluded" holds for the two variants of C
> this document actually documents — `SetCursor(NULL)` and CSS
> `cursor: none` — because *both* install a **NULL platform cursor**,
> and a NULL platform cursor comes back as a NULL `hCursor`. A real
> handle therefore excludes both of them.
>
> It does **not** exclude every possible app-level hide, and two
> Chromium specifics say so:
>
> - Chromium's `kHideCursorWhileTyping` (FEATURE_ENABLED_BY_DEFAULT on
>   Windows) hides the pointer by setting a `kNone` **platform** cursor
>   through `DesktopNativeCursorManager::SetVisibility`
>   (`ui/views/widget/desktop_aura/desktop_window_tree_host_win.cc`),
>   **not** by calling `::ShowCursor` — so the display counter is not
>   what changed.
> - Chromium's own upstream comment describes `ShowCursor`'s counter as
>   "the Win32 **thread-global** cursor display counter" — i.e.
>   **per-thread** — while `CURSORINFO` is documented as **global**. A
>   read taken from another thread therefore cannot exclude a
>   thread-local hide inside the app.
>
> That path is excluded here on its own terms, not by the table: the
> double gate in `wm::CursorManager::ShouldHideCursorOnKeyEvent`
> requires **mouse-vanish** (off on this machine — see the ruled-out
> table) *and* a character key-down with a non-NONE `TextInputClient`;
> the affected surfaces are not text inputs; and it is whole-app, not
> region-scoped. The point of recording it is that the table is a
> **discriminator, not a proof**.
>
> So the pointer-trails test below is still needed, but only to separate
> **A from B**. To separate **C from both**, run
> `scripts/cursor_shapes.py --watch 120` and hover each affected
> surface; the census prints a verdict for each one.
>
> The full test list is in
> [the fixes section](#0-the-by-eye-tests--do-these-first-they-cost-nothing-and-they-decide-the-mechanism).

**The Explorer navigation pane is now measured, and it is not C.** A
probe over the nav-pane tree at (581, 302) returned

```
hCursor = 0x1001F   (HAND - the pointing hand - a real handle)
flags   = 0x00000001 (CURSOR_SHOWING set, CURSOR_SUPPRESSED clear)
```

and over the neighbouring file list at (982, 478) `0x10003`
(`IDC_ARROW`) with the same flags. The OS **had** a cursor there and
reported it as showing. So over the nav pane the fault is in the
**display path** — and because the nav pane sits on the **primary**,
where **A** is capability-excluded (§3), that narrows to **B**. Note
also what the handle says: the shell's tree view **handles
`WM_SETCURSOR` and sets a hand cursor of its own**, which is exactly the
"app sets the cursor itself" case that a NULL class cursor obliges it
to handle (§3b).

> **How this reading was taken — and the one synthetic input in the
> whole investigation, which is *not* in this reading.** The block above
> came from `scripts/cursor_shapes.py`, which is a **pure read**: it
> calls `GetCursorInfo` at the pointer's current position and never
> calls `SetCursorPos`. The pointer was simply where you had left it —
> over the nav-pane tree. Nothing moved it.
>
> The synthetic input is in a *different* probe. `scripts/cursor_is_composited.py`
> (and its predecessor `cursor_software_probe.py`) has a `jiggle(x, y)`
> helper that calls `SetCursorPos(x + 1, y)` and then `SetCursorPos(x, y)`
> — it shifts the pointer **one pixel** right and immediately back (50 ms),
> a controlled test of whether the arrow is composited into the
> framebuffer. It moves the pointer only from the position it was already
> at, and returns it to that exact position; it cannot and does not
> place the pointer anywhere. No click, key, scroll or window move was
> synthesised; no window was activated, raised or closed. This is
> disclosed here because the read-only discipline for the rest of the
> investigation was stricter than this one step, and because a reader
> checking that claim should find it broken in the open rather than have
> to find it (§"How these facts were established").

**What the suppression measurement does and does not settle.** It
settles that *at that position* the application had asked for no
cursor. It does **not** yet say which of the two variants of C it was,
nor whether the Windows Search results list and the VS Code tree
behave the same way — those need the pointer sampled over them. It is,
however, enough to retire the previous "no app called `SetCursor(NULL)`
in any of them" claim, and enough to show that the *same* Chromium app
shows a live arrow over one region and suppresses the cursor over
another (284 samples showed a live arrow at other positions in that
same app).

> ### ⚠ What the C sample does and does not cover — read this before you act on it
>
> The one C sample was taken in **Brave** at **(901, 660)**. Brave is
> **not** one of the surfaces the user reported (see the symptom table),
> and this document never records whether the pointer was *actually
> invisible* at that position. The 2956 samples establish that the OS
> held **no cursor** there — not that the user was looking at a blank
> patch of screen.
>
> **Nothing in this document has ever measured the pointer's
> *visibility* at any position.** Every "visible" statement in it is
> either the user's own report or an inference from a cursor handle
> being present — and a handle is not a pixel.
>
> The one reported surface that **has** now been sampled is the
> **Explorer navigation pane**, and it came back **not C** (a real,
> showing hand cursor — see §Verdict). So the nav pane is a
> *display-path* fault — and it is on the **primary**, where **A** is
> capability-excluded (§3), so that leaves **B** — even though its
> neighbouring file list shows a perfectly visible arrow. That is the
> sharpest instance of the symptom in the whole investigation, and it is
> the one surface where mechanism C is now **ruled out**.
>
> So the C finding is, so far, *the existence of a cursor-suppressing
> region in a Chromium app* — not *the explanation of the reported
> symptom*. For C to be the answer to a reported surface, two things
> have to hold there: the probe reports `MECHANISM C` **and** the
> pointer is visibly missing. The cheapest way to check the second half
> is by-eye test **0a** (pointer trails ON, which forces the software
> cursor path so the arrow is visible at all) or a phone photo — a
> still screenshot will not contain the arrow (§2).

### Why mechanism A does not fit — MPO is per *output*, not per adapter

`dxdiag` reports overlay capability **per output**, and the two outputs
of this RTX 2060 differ:

| Output | Panel | Bounds | `MPO MaxPlanes` | `MPO Caps` |
|---|---|---|---|---|
| `\\.\DISPLAY1` **(primary)** | MSI B27T-7 LED, DisplayPort, 1920×1080 | (0,0)–(1920,1080) | **1** | **Not Supported** |
| `\\.\DISPLAY2` (secondary) | HP P221, HDMI, 1536×864 @125 % | (1920,0)–(3456,864) | **4** | RGB, YUV, BILINEAR, HIGH_FILTER, STRETCH_YUV, STRETCH_RGB, IMMEDIATE, HDR (MPO3) |

`MPO MaxPlanes` **counts the primary surface as one of the planes**, so
`MaxPlanes: 1` means "the primary/root surface only — **zero overlay
planes**", and `MaxPlanes: 4` means the primary plus three overlay
planes. (Microsoft: the maximum is "the maximum number of inputs,
including the primary surface, to the display hardware that can be
supported in the current mode" — "if the hardware allows one overlay
plane and one normal primary surface, the driver should set MaxPlanes
to 2".) And "Not Supported" is not a per-feature verdict: it is the
whole caps row rendering empty — on an output with no MPO, `MPO Caps`,
`MPO Stretch`, `MPO Media Hints` and `MPO Formats` all read "Not
Supported".

The report is **per output and mode-dependent**, so these figures
describe the current display configuration, not the hardware in
general: Microsoft notes that MPO capabilities "are allowed to change
due to display configuration changes", and in clone mode "hardware
shouldn't report that it supports multiplane overlays unless all active
outputs support multiplane overlays". The primary's reading could
change with a refresh-rate or mode change, a clone configuration, or
with the second display disconnected.

```
HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\FeatureSetUsage
    AdapterMPO_3 = 1        (undocumented; the ADAPTER supports MPO — not the primary OUTPUT)
```

`FeatureSetUsage` / `AdapterMPO_3` are **undocumented** — Microsoft
publishes nothing about them. The name indicates adapter-level MPO
support; the "_3" most likely denotes the MPO DDI feature-set version
(`D3DKMT_MULTIPLANE_OVERLAY_CAPS.Version3DDISupport` = "Driver
supports the WDDM 2.2 MPO (multi-plane overlay) DDIs"), which matches
the "(MPO3)" tag in the very same dxdiag `MPO Caps` string above. It
is **not** an adapter index and **not** an output identifier — this
machine has one GPU and exactly one `AdapterMPO_3` value in the whole
`HKLM\SYSTEM\CurrentControlSet\Control` tree — so it is a single
machine-wide entry and it says **nothing about which outputs have
planes**.

What the dxdiag numbers do show is that the **primary output currently
reports no overlay planes**, so on the primary display there is nothing
that could be scanned out above the cursor.

**Re-measured directly, per VidPN source.** `dxdiag` is a rendering of
the driver's answer, not the answer itself: the real query is
`D3DKMTGetMultiPlaneOverlayCaps`, which is keyed by `hAdapter` **plus
`VidPnSourceId`** — that is the API-level statement of "MPO capability is
per *output*", and it is what
[`D3DKMT_GET_MULTIPLANE_OVERLAY_CAPS`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_get_multiplane_overlay_caps)
takes. Re-running that probe on this box (read-only) reproduces dxdiag
exactly:

```
src=0 (primary, DisplayPort)  MaxPlanes=1  caps=0x00000A00  stretch=1.000 shrink=1.000
src=1 (secondary, HDMI)       MaxPlanes=4  caps=0x00000AF0  stretch=10.000 shrink=0.500
```

Two things follow, and both are stronger than the dxdiag rendering:

- The primary's caps lack `StretchRGB` / `Bilinear` / `HighFilter`, and
  they fail the documented hardware requirement that "at least one plane
  must support shrinking and stretching" ([MPO Hardware
  Requirements](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/multiplane-overlay-hardware-requirements)).
  `MaxPlanes=1` is plane 0 only — the primary surface — so **zero**
  overlay planes exist on the primary output.
- `IDXGIOutput2::SupportsOverlays` returns **FALSE for both outputs** on
  this machine (it is `IDXGIOutput2` vtable slot 23; slot 16 is
  `SetDisplaySurface`). An application cannot promote a swap chain to an
  overlay plane here at all.

**And no documented cursor layer exists in the overlay z-order.** The
overlay plane list (`DXGK_MULTIPLANE_OVERLAY_PLANE.LayerIndex`) contains
**no cursor layer**, and the MPO hardware-requirements page never uses
the word "cursor" at all. So mechanism A — "a plane scanned out *above*
the cursor" — is not merely unsupported on the primary, it is
**not expressible in the documented model**. It survives only as a
*whole-window* hypothesis on the HDMI output, and even there it rests on
an undocumented assumption (see §2).

> **A claim this document used to make, now withdrawn.** It used to say
> "the hardware cursor is its own plane, distinct from plane 0". That is
> an **assumption, not documentation** — no Microsoft document places the
> hardware cursor in the overlay z-order. What *is* documented are four
> cursor↔plane ties, all of them about **how the cursor is scaled or
> blended**, not about where it sits:
>
> - `Plane0ForVirtualModeOnly` — "the hardware will always apply the
>   stretch factor of plane 0 to the hardware cursor as well as the
>   plane";
> - `DXGK_DRIVERCAPS.CursorScaledWithMultiPlaneOverlayPlane0`;
> - `DXGK_DRIVERCAPS.MiscCaps.CursorDoesNotSupportXorBlendWithMultiPlaneOverlay`
>   — a field name that itself asserts the cursor *is* blended with an
>   MPO plane;
> - the per-output `DXGI_HARDWARE_COMPOSITION_SUPPORT_FLAG_CURSOR_STRETCHED`
>   (on this box: DISPLAY1 → `0x5` = FULLSCREEN | CURSOR_STRETCHED;
>   DISPLAY2 → `0x3` = FULLSCREEN | WINDOWED).
>
> The cursor's plane identity and z-position are a **driver and GPU
> decision**. That is exactly why mechanism A cannot be *confirmed*
> anywhere, and why it cannot be *refuted* by the absence of a
> documentation sentence either.

And the symptom is **intra-window**: in the *same* Explorer window the
pointer is visible over the file list and invisible over the navigation
pane; in the *same* VS Code window it is invisible over the file-explorer
tree. A promoted swap chain occupies **one rectangle** on its plane, and
DWM's desktop swap chain is per *output* — so **a plane whose promoted
surface is the whole window cannot split that window** into "cursor
visible here, invisible there". A *sub-rectangle* of a window is not
excluded by that argument, though: this document's own §3 census lists
DirectComposition child surfaces (`FileExplorer_FolderViewIsland` →
`Microsoft.UI.Content.DesktopChildSiteBridge` →
`InputSiteWindowClass`, all `WS_EX_NOREDIRECTIONBITMAP`), each with its
own swap chain, and nothing in the DDI documentation rules out promoting
one of those.

**So: mechanism A can only be part of the story on the secondary
(HDMI) output — as currently reported — and cannot be the whole story
at all.** That is a correction to an earlier version of this document,
which asserted "MPO is the mechanism" on the strength of
`AdapterMPO_3 = 1`.

**Microsoft has already fixed something upstream** — the fix notes for
Insider build 26340.9233 read *"Fixed an issue in the latest builds
causing the mouse cursor not to be visible in certain apps"*, which
describes exactly this symptom class and, crucially, describes it as
**app-dependent**. That fix is not in the GA build this machine runs
(26300.9711 / KB5127648). See
[Microsoft has already fixed it](#microsoft-has-already-fixed-it-upstream).

## What was measured on this machine

### 1. The OS-level pointer is healthy *where it was sampled* — and applications can suppress it

Across **8296 live samples** (`GetCursorInfo`), taken over the Terminal,
VS Code, several Chromium apps, a `webview` host, dialogs and the
taskbar:

- `CURSOR_SHOWING` (0x1) was **always** set
- `CURSOR_SUPPRESSED` (0x2) was **never** set
- `hCursor` was **never** NULL and never a blank cursor

Confirmed again on a further 401 samples over 14 distinct surfaces, and
again over **695 samples in a later run** whose live cursor handles
decoded to `IDC_ARROW` (0x10003), `IDC_IBEAM` (0x10005) and the
pointing **hand** (0x1001F) — every one of them a real, shown cursor.

> ### ⚠ But that claim was over-general, and a targeted probe falsified it.
>
> "No app called `SetCursor(NULL)` in any of them" was true **of the
> samples** and false **as a general statement**. A probe aimed
> specifically at the current pointer position over a Chromium render
> widget (Brave, pid 18400) returned
>
> ```
> hCursor = 0x0            (NULL)
> flags   = 0x00000000     (CURSOR_SHOWING clear, CURSOR_SUPPRESSED clear)
> ```
>
> stably, over **2956 samples in 90 seconds** — while 284 samples over
> other positions in the *same* application showed a live arrow. The
> application suppresses the cursor **per region**, which is exactly the
> "same window, different column" symptom.
>
> In a Chromium/Electron renderer this is now traceable to source:
> CSS `cursor: none` becomes `CursorType::kNone`, Chromium's
> `WinCursorFactory::GetDefaultCursor` returns a **NULL** `HCURSOR` for
> it ("Better to totally remove it from the screen"), and
> `WinWindow::SetCursor` then calls `::SetCursor(nullptr)` — which MSDN
> documents as "the cursor is removed from the screen". The *converse*
> matters too: a **custom** CSS cursor (`cursor: url(...)`) becomes a
> **real, non-NULL** `HCURSOR` that the OS draws, so a custom cursor can
> never be mistaken for a suppressed one by this probe — and that is
> what the 1259 "CUSTOM" samples in the two-hour census were.
>
> `CURSOR_SUPPRESSED` (0x2) was never set in any sample: that flag
> means the pointer is being driven by **touch or pen** input, not that
> the cursor is hidden. Hiding shows up as a NULL handle with
> `CURSOR_SHOWING` clear — a different signal entirely, and the one
> that was being missed.

### 1b. Decoding the cursor handles — what was actually under the pointer

`watch_cursor5.py` recorded the live cursor as an opaque handle. The
handles are **shared objects**, so the same value means the same cursor
in every process, and the shared cursor table is allocated in system
cursor order. `scripts/cursor_handle_map.py` reads the mapping
directly by loading each standard cursor and printing its handle. The
mapping was re-measured on this machine with the **correct** `IDC_*`
values from `WinUser.h` — and the complete table is:

```
  0x10003  ARROW       (IDC_ARROW      32512)   32x32  opaque=144  hot=(0,0)
  0x10005  IBEAM       (IDC_IBEAM      32513)   32x32  opaque=1024 hot=(8,9)
  0x10007  WAIT        (IDC_WAIT       32514)   32x32  opaque=348  hot=(16,16)
  0x10009  CROSS       (IDC_CROSS      32515)   32x32  opaque=1024 hot=(16,16)
  0x1000B  UPARROW     (IDC_UPARROW    32516)   32x32  opaque=81   hot=(4,0)
  0x1000D  SIZENWSE    (IDC_SIZENWSE   32642)   32x32  opaque=115  hot=(8,8)
  0x1000F  SIZENESW    (IDC_SIZENESW   32643)   32x32  opaque=115  hot=(8,8)
  0x10011  SIZEWE      (IDC_SIZEWE     32644)   32x32  opaque=117  hot=(11,4)
  0x10013  SIZENS      (IDC_SIZENS     32645)   32x32  opaque=117  hot=(4,11)
  0x10015  SIZEALL     (IDC_SIZEALL    32646)   32x32  opaque=225  hot=(11,11)
  0x10017  NO          (IDC_NO         32648)   32x32  opaque=222  hot=(8,8)
  0x10019  APPSTARTING (IDC_APPSTARTING 32650)  32x32  opaque=364  hot=(0,8)
  0x1001B  HELP        (IDC_HELP       32651)   32x32  opaque=202  hot=(0,0)
  0x1001F  HAND        (IDC_HAND       32649)   32x32  opaque=285  hot=(6,0)
  0x10025  PIN         (IDC_PIN        32671)   32x32  opaque=459  hot=(6,0)
  0x10027  PERSON      (IDC_PERSON     32672)   32x32  opaque=378  hot=(6,0)
```

> **A correction that matters, and it came from a wrong `IDC_*` table.**
> An earlier version of this document said the size cursors, `NO` and
> `HAND` "fail to load with `ERROR_RESOURCE_TYPE_NOT_FOUND` 1814", and
> that `0x1001F` had to be named **from its glyph** because
> `LoadCursor` could not load it. **Both were false.** The `IDC_*`
> values in the first group run 32512..32516 and the second group
> starts at **32640**, not 32517 — so the contiguous
> 32517..32529 numbering (which an earlier revision of this document
> and of `cursor_shapes.py` used) simply names ids that do not exist.
> `LoadCursor` failed on **the wrong ids**, and the failure was read
> as a property of the build. With the correct values every slot above
> loads and names itself. `IDC_SIZE` (32640) and `IDC_ICON` (32641)
> are marked *obsolete* in the header and genuinely do not load.
> There is no `IDC_QUESTION` in the current header at all, and
> `IDC_PIN` is 32671 and `IDC_PERSON` is 32672 — **both shared**, not
> the non-shared handles an earlier sweep reported for 32652/32653/
> 32654 (which are not cursor ids).
>
> **Two rules that fall out of this.** The shared table has **no
> gaps** — every slot from `0x10003` to `0x10027` is accounted for. And
> its order is the **system cursor table** order, *not* the `IDC_*`
> order: `HAND` is `IDC_HAND` 32649 yet sits at `0x1001F`, *after*
> `APPSTARTING` (32650, `0x10019`) and `HELP` (32651, `0x1001B`).
> Never extrapolate a handle from an offset.

So the watcher's readings decode as:

| Handle | Cursor | Where it was seen |
|---|---|---|
| `0x10003` | **ARROW** | Terminal, taskbar, and most Chromium samples |
| `0x10005` | **IBEAM** (text select) | 32 samples over Brave, 19 over Hermes — i.e. over *text* |
| `0x1001F` | **HAND** (link cursor) | 3 samples over Brave, and over the Explorer navigation pane |

The `0x0` — **no cursor at all** — is **not** in this table: it was
measured by the separate `cursor_shapes.py` probe (§1, §Verdict), which
found `hCursor = 0x0` with `CURSOR_SHOWING` clear over a Brave render
widget. In the 695 `watch_cursor5.py` samples the live cursor was a
real, shown handle in **every single one**.

> **And this invalidates one label in the census.** The two-hour census
> reported "1259 samples of a **custom** cursor" in Brave. With the
> broken `IDC_*` table, a link hand could not be recognised and fell
> into the `CUSTOM` bucket — so **that bucket cannot be trusted**: it
> may be a mixture of the app's own custom cursor and ordinary `HAND`.
> `scripts/cursor_shapes.py` has been fixed, and it now names `HAND`
> correctly; **re-run the census before drawing any conclusion from
> the CUSTOM rows.** (The `MECHANISM C` rows are unaffected — they are
> read from the *flags*, not from the glyph.)

**This is worth stating plainly: over the 284 Brave samples the watcher
recorded, the pointer was not "invisible" in the display sense.** The
OS had a real cursor to draw — an arrow, an I-beam, a link hand — and
did draw it. (The separate probe in §1 did find a Brave render widget
where the OS held *no* cursor at all, so "Brave was always fine" is no
longer true either; and whether the pointer was visibly missing at that
position was never recorded.) What the user sees missing in the
*reported* surfaces is a different thing, and this table is the evidence
that the Chromium surfaces are not all alike.

### 2. The pointer is on the hardware cursor plane — and the framebuffer route is blind to *why* it vanishes

> **One qualification on the hardware-plane claim.** "The pointer is on
> the hardware cursor plane" is what was *measured here*, on this
> machine, with this pointer — it is **not a law of Windows**. The
> software-cursor path exists and is reachable: **animated cursors**
> (Chen's own example, below), a driver that calls `EngSetPointerShape`
> so that "GDI manage[s] a software cursor", a full-screen **Direct3D 9**
> app where "DirectX uses an emulated cursor", a **Windows.Graphics.Capture**
> session opened with `IsCursorCaptureEnabled = true` (per the capture
> API's own maintainer, "engaging the capture API will force the system
> into software cursor mode"), **pointer trails** (test 0a below), and
> remote-desktop or virtual sessions, where the pointer is drawn by the
> client. The presence or absence of the arrow in any capture is
> therefore a function of **which of these paths is active** — not of
> the cursor's shape.
>
> **A second qualification, added later.** "On the hardware cursor
> plane" is a *measurement* of where the arrow's pixels live. It is
> **not** a claim that the cursor occupies a documented plane with a
> documented z-position relative to overlay planes — no Microsoft
> document places it in that z-order. See the withdrawn claim in
> [Why mechanism A does not fit](#why-mechanism-a-does-not-fit--mpo-is-per-output-not-per-adapter).

The arrow glyph was reconstructed from the live cursor's own bitmaps
(`GetIconInfo` → `GetDIBits`) and template-matched against a 64×64
`BitBlt` of the screen DC with `SRCCOPY|CAPTUREBLT`
(`scripts/cursor_is_composited.py:160`) at the pointer, split into the
glyph's dark outline and its light fill, with control samples taken
64 px away:

```
AT the pointer (1685,1)   : dark 0/67   light 0/76
control  +0, -64          : dark 67/67  light 0/76
control  -64, +0          : dark 0/67   light 0/76
```

> ### ⚠ The first calibration was void. Here is the corrected instrument, and the corrected result.
>
> An earlier version of this document read that block as "the arrow is
> not in the screen image at all — the controls score differently from
> the pointer, so the test discriminates". **It did not.** The
> instrument was then "calibrated" with a solid magenta 32×32 square
> set as a *non-system* cursor, of which **0 pixels** appeared in a
> screen grab — and the document concluded that the reader was blind to
> software cursors.
>
> **That calibration was void, and this is why.** `SetCursor` sets the
> cursor shape for the windows **owned by the calling thread**; a
> console script owns no window at the pointer's position, so the
> magenta cursor was never drawn. `scripts/cursor_is_composited.py`
> checks that directly, and it is unambiguous:
>
> ```
> SetCursor() returned      : 0x10007    (the PREVIOUS cursor handle)
> GetCursorInfo().hCursor   : 0x10005    (unchanged - the system I-beam)
> our magenta handle        : 0x12010E87
> magenta pixels in the grab: 0
> => SetCursor was IGNORED - the cursor under the pointer never changed
> ```
>
> So "0 magenta pixels" measured **nothing**. The claim "the reader
> cannot see software cursors" was never established.
>
> **The replacement instrument needs no input synthesis at all:** match
> the cursor's own bitmaps against the screen at the pointer's exact
> position, and then at the same crop **shifted by N pixels**. A real
> composited arrow should match *both* halves at offset 0 and not at
> the shifted offsets. (Note: the arrow's dark class is **2-3 pixels
> wide** — two near-opaque outline columns plus a soft drop shadow —
> so the *dark* half does **not** collapse at a 1- or 2-pixel shift,
> even for a genuinely composited arrow. The *light* half is the
> discriminating signal.)
>
> ```
> offset ( +0, +0)  dark 0.88  light 0.06   <-- exact position
> offset ( +1, +0)  dark 0.85  light 0.06
> offset ( -1, +0)  dark 0.58  light 0.05
> offset ( +2, +0)  dark 0.92  light 0.06
> offset ( +4, +0)  dark 0.96  light 0.07
> offset ( -4, +0)  dark 0.69  light 0.06
> offset ( +8, +0)  dark 0.69  light 0.06
> offset ( -8, +0)  dark 0.81  light 0.07
>
> exact position : dark 0.88  light 0.06
> best off-centre : dark 0.96  light 0.07
> ```
>
> **The arrow is not in the image.** One reason is sufficient, and it
> is the first:
>
> 1. **The light half scores 0.05–0.07 at every offset** (0.06 at the
>    exact position, and never higher than 0.07 off-centre). A real, composited
>    two-tone arrow matches *both* halves at once (§2b) — the white
>    body reads **1.00** at offset 0 over a light, a dark *and* a
>    mid-tone background. The white body is simply not there.
>
> The other two observations are **not independent reasons**, and are
> recorded here so nobody repeats them as if they were:
>
> 2. **Offset 0 does not dominate on the dark half** — the *best*
>    off-centre offset scores **higher** (0.96) than the exact position
>    (0.88). Do not read this as proof that the score is background:
>    the arrow's dark class is 2-3 px wide, so a composited arrow's
>    dark score does **not** collapse at a two-pixel shift (measured on
>    a synthetic composited arrow over a dark background: 0.72 at +1,
>    0.72 at +2, 0.54 at −2). The asymmetry — +4 scoring 0.96 and −4
>    scoring 0.69 — is a dark region offset to one side of the
>    pointer, which is what an unrelated background looks like.
> 3. The `dark 0/67` in the original sample was the same thing: over a
>    **bright** background the arrow's dark outline does not match;
>    and `dark 67/67` at the control is not a decoy arrow either — a
>    **uniformly dark** region scores a *perfect* match on the arrow's
>    dark outline and a zero match on the light body. That is exactly
>    the `ctrl 1.00 / light 0.08` seen in the live runs: the control
>    is a dark background, not an arrow.
>
> **What this now establishes (a real measurement, with a working
> instrument):** the pointer's pixels are **not in the framebuffer**,
> over a GDI surface *and* over an accelerated Chromium surface
> alike. That is what a pointer on the adapter's **hardware cursor
> plane** looks like, and it is why the document's own measurement
> could never demonstrate *why* the pointer is invisible.
>
> **Caveat on the instrument — it is a sound positive test, not a
> sufficient negative one.** `scripts/cursor_is_composited.py:294`
> decides with a single **conjunction** —
> `d0 > best_d + 0.10 and l0 > best_l + 0.10` — and the `0.10` margin
> is arbitrary. Run against a **synthetically composited** arrow it
> reports "not composited" over a light background (exact 0.37 / 1.00)
> and over a dark background (exact 1.00 / 1.00), and fires only over
> a mid-tone background (exact 0.70 / 1.00). The **light** half is
> what discriminates here (1.00 at offset 0 in every background
> tested); the dark half is not. A run that wants to be conclusive
> should also draw the arrow somewhere known to be visible and require
> the sweep to detect it there — if it does not, the run is void.
>
> **One caveat on the grab itself.** It is `SRCCOPY|CAPTUREBLT`, so it
> reads the **composited** output rather than the raw framebuffer. Per
> Chen's article, "the mouse cursor is just another composition object
> and therefore would be captured by the CAPTUREBLT flag. To prevent
> this from happening during a screen capture, the composition engine
> has to hide the cursor, do the CAPTUREBLT, and then re-show the
> cursor." So the composition engine hides the cursor for the duration
> of the grab, and a negative result from this sweep does **not** by
> itself distinguish a hardware-plane pointer from a software one. The
> hardware-plane conclusion rests on Chen's separate statement about
> hardware cursors and on the `GetCursorInfo` / `CURSOR_SHOWING`
> readings, not on this sweep alone.
>
> **What it still never demonstrates:** anything about the cause. The
> framebuffer cannot see the cursor, so it cannot distinguish
> "covered" from "not refreshed". Only a **by-eye** test can.

The correct primary source for the hardware-cursor fact is Raymond
Chen, **"Windows Confidential: The Case of the Disappearing Cursor"**,
*TechNet Magazine*, February 2009 (page last updated 2016-09-08)
(→ <https://learn.microsoft.com/en-us/previous-versions/technet-magazine/dd392008(v=msdn.10)>):

> When a hardware cursor is employed, the pixels of the mouse cursor
> do not actually exist in the frame buffer because the hardware is
> responsible for performing the overlay. Consequently, the BitBlt
> function is able to just copy pixels from the frame buffer without
> fear of picking up pixels from the mouse cursor by mistake since
> those pixels don't exist in the frame buffer to begin with.

The same article states the converse, which bounds our own claim:

> Conversely, if a software cursor is being used, then the GDI must
> remove the mouse cursor from the screen before performing a BitBlt
> from the screen if the region being copied overlaps the mouse
> cursor.
>
> When animated cursors are employed, hardware cursors just don't
> quite cut the mustard, since hardware cursors don't animate.
> Therefore, animated cursors are implemented in software.

Two consequences worth stating plainly:

- **A *still* screenshot missing the arrow is not evidence of a bug.**
  It is what a hardware cursor looks like in a capture. (A
  **screen recording** is different: the Snipping Tool's record mode
  and Game Bar's recorder both contain the pointer, and a
  `Windows.Graphics.Capture` session opened with
  `IsCursorCaptureEnabled = true` "will include the cursor in the
  captured content" — but engaging the capture API "will force the
  system into software cursor mode", so it changes the very thing
  being diagnosed. Third-party tools that offer an "include cursor"
  option (ShareX, Greenshot, PicPick) draw the arrow themselves from
  `GetCursorInfo`/`GetIconInfo`, so their output says nothing about
  the framebuffer either.)
- **Pointer trails force the software-cursor path** — a hardware cursor
  is a single sprite and cannot leave copies of itself behind — and
  that makes them the cheapest possible by-eye test of whether
  anything is covering the pointer (see
> [the fix section](#the-fixes-cheapest-first)). ***This is not from
> Chen's article.*** His only software-cursor example is **animated**
> cursors (quoted above). The trails behaviour is a separate, real
  Windows behaviour — but note that
  [microsoft/vscode#239110](https://github.com/microsoft/vscode/issues/239110),
  *"Activating display pointer trails makes text editing pointer
  disappear"*, is a **Chromium cursor-rendering** report: it
> corroborates that trails change how the pointer is drawn, not that
> Windows switched to the software-cursor path.

**Consequence for the diagnosis:** a hardware-plane pointer can vanish
in three ways — a display plane is scanned out **above** the cursor
plane (mechanism A), the cursor plane is simply **not refreshed** over
that region (mechanism B), or the **application** suppresses the cursor
over that region (mechanism C). **The framebuffer measurement cannot
separate them**: the pointer is not in the framebuffer either way. The
GDI screen-DC route and the DXGI-duplication route are blind to the
hardware cursor (the latter delivers the pointer shape *out of band*,
via `IDXGIOutputDuplication::GetFramePointerShape`), and the
`CAPTUREBLT` route — which does read the composited output — is also
blind here, because the composition engine hides the cursor for the
duration of the grab. But the *cursor-state* read does separate **C**
from A and B — one `GetCursorInfo` call decides C (§Verdict, §3b) — so
only **A-versus-B** still needs the user's eyes.

### 2b. The arrow is two-tone, so it cannot be hidden by contrast

The live arrow has **67 dark-class pixels and 76 light-class pixels** —
but they are **not equally solid**. The **76 light pixels are fully
opaque** (alpha 255): that is the white body. The **67 dark pixels are
all partially transparent** (alpha 26 to 244, **none at 255**): they are
the anti-aliased black outline plus the soft drop shadow, and they span
**two to three pixel columns**, not a single one. Either way the arrow
cannot be hidden by contrast:

- a light background leaves the near-opaque outline columns visible
  (alpha ≈ 210 and ≈ 240) and the white body still reads as the
  brightest thing on screen,
- a dark background leaves the white body visible,
- a mid-tone background makes both visible.

There is no background that hides this arrow. Wherever a cursor exists,
whatever makes it disappear is **covering** it, not camouflaging it —
in mechanism C there is no cursor at all, so there is nothing to cover
(§3b).

**Consequence:** changing the pointer size, the pointer theme or adding
a shadow cannot help. Those are listed in some upstream reports as
"workarounds", but they only address a contrast problem, and there
isn't one here. Where the pointer is on the hardware cursor plane, it is
being overwritten in the display pipeline (mechanisms A and B); in
mechanism C it is never drawn at all.

*(This argument does not depend on the framebuffer measurement — it is
a property of the arrow's own bitmaps, read straight out of the live
cursor with `GetIconInfo` → `GetDIBits`.)*

### 2c. The live cursor was healthy at every surface *that run* sampled

A 50-minute run of `scripts/watch_cursor4.py` collected **401 samples
over 14 distinct surfaces** (Windows Terminal, VS Code, several
Chromium apps, a `webview` host, dialogs, the taskbar):

- The live cursor was a **real system cursor in every single sample** —
  `0x10003` (ARROW), `0x10005` (IBEAM), `0x10007` (WAIT), `0x10009`
  (CROSS), `0x1000B` (UPARROW) and `0x1001F` (HAND) — with
  `CURSOR_SHOWING` always set and `CURSOR_SUPPRESSED` never set. (The
  full handle↔name table is in §1b; `0x1001F` is `IDC_HAND`, 32649.)
- The glyph's *light* match rate at the pointer was `0.00` everywhere,
  including over **VS Code's Chromium render widget**
  (`Chrome_RenderWidgetHostHWND` in `Code.exe`, PID 35572,
  `WNDCLASS.hCursor == NULL` — the exact surface the pointer vanishes
  on).

  *How much that carries.* **Less than it used to.** §2 established
  that the screen reader cannot see a cursor at all, so the `0.00`
  light scores are **uninformative**: they are what a healthy,
  correctly-rendered pointer would also produce. The first half of
  this bullet (`CURSOR_SHOWING` always set, `CURSOR_SUPPRESSED` never
  set, a real system cursor every time) stands on its own and is
  solid. The second half does not.

So over a surface where the arrow is invisible, Windows *may* still
report a normal, un-suppressed, un-blanked pointer, and the pointer is
then still on the hardware cursor plane. But that is **no longer the
general case**: the targeted probe in §1 found a Chromium render widget
where the OS reported **no cursor at all** (§Verdict), so on that
surface there is nothing on the hardware cursor plane and nothing
being covered. The pointer is healthy **where a cursor exists**; it is
either covered or not refreshed (A/B), **or the application never asked
for one (C)**.

(One app — Hermes, PID 34764 — sets an app-supplied custom cursor
handle, `0x3CFB069B`, rather than a system one. That is normal
behaviour for a Chromium app and does not hide the pointer.)

### 2d. The mechanism, in Microsoft's own words — and where the words stop

> **This section previously read as if the occlusion mechanism were
> Microsoft-documented. Two independent adversarial verifications
> refuted that reading, and it is corrected below.**

- **MPO is hardware compositing of several planes into one image.**
  *MPO Support* (Windows drivers) describes it as the graphics
  hardware *"compos[ing] multiple layers of content into a single
  image"*, where *"a plane can be a video, the desktop, an application
  window, and so on"*.
  → <https://learn.microsoft.com/en-us/windows-hardware/drivers/display/multiplane-overlay-support>

- **The DWM's swapchain is one of those planes, and the planes are
  blended in z-order.** *Multiplane Overlay Hardware Requirements*
  states that *"the Desktop Window Manager (DWM)'s swapchain (plane 0)
  must be able to interact with the other overlay planes"*, and that
  blending *"should be performed from top to bottom (or until an opaque
  layer is hit) in z-order"*, with `Layer[0]` the topmost layer.
  → <https://learn.microsoft.com/en-us/windows-hardware/drivers/display/multiplane-overlay-hardware-requirements>

- **The z-order is explicit, and index 0 is the top.** The
  `_DXGK_MULTIPLANE_OVERLAY_PLANE` structure documents `LayerIndex` as
  *"the zero-based index of the overlay plane to display"* and states:
  *"The top plane (in the z-direction) has index zero. The planes'
  index values must be sequential from top to bottom."*
  → <https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmddi/ns-d3dkmddi-_dxgk_multiplane_overlay_plane>

- **The hardware cursor is not in that ordering at all.** The cursor
  has **no `LayerIndex`** and is not an entry in
  `_DXGK_MULTIPLANEOVERLAYCAPS` or `_DXGK_MULTIPLANE_OVERLAY_PLANE`.
  It is programmed through a separate DDI pair,
  `DxgkDdiSetPointerPosition` / `DxgkDdiSetPointerShape`, which
  Microsoft describes as *"called independently of all of the other
  display miniport driver functions"* — i.e. **outside the MPO plane
  machinery**. The word "cursor" does not occur anywhere in the MPO
  Support, VidPN Presentation or Hardware Requirements pages.

- **And the one Microsoft sentence that does mention the cursor says
  nothing about z-order.** `DXGK_MULTIPLANEOVERLAYCAPS.Plane0ForVirtualModeOnly`
  says: *"When TRUE, the hardware will always apply the stretch factor
  of plane 0 to the hardware cursor as well as the plane. This implies
  that stretching/shrinking of plane 0 should only occur when plane 0
  is the desktop plane and when the stretching/shrinking is used for
  virtual mode support."* That is about **panel-fitting / virtual-mode
  stretch scaling**, not about scanout occlusion; it says nothing about
  where the cursor sits in the plane z-order, and it is not evidence of
  a separate z-ordered plane.
  → <https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmddi/ns-d3dkmddi-_dxgk_multiplaneoverlaycaps>

- **"Plane 0" and `Layer[0]` are two different index spaces — and the
  overlay planes sit *above* the desktop.** On the Hardware Requirements
  page "plane 0" is the desktop's scanout plane: *"the Desktop Window
  Manager (DWM)'s swapchain (plane 0) must be able to interact with the
  other overlay planes"*, *"All planes must be able to be enabled and
  disabled, including plane 0 (the DWM's swapchain)"*. The overlay
  planes are "the other" ones. `LayerIndex`, by contrast, numbers only
  the **overlay** planes: it is a member of `_DXGK_MULTIPLANE_OVERLAY_PLANE`,
  the element type of `_DXGKARG_SETVIDPNSOURCEADDRESSWITHMULTIPLANEOVERLAY.pPlanes`
  (*"specifies the first overlay plane to display"*), whose `PlaneCount`
  is *"the number of overlay planes that the hardware supports"*. The
  desktop swapchain is not an entry in that overlay-plane array, so
  "plane 0 = `Layer[0]`" does not follow. The model rules the literal
  reading out: an overlay plane's blend mode *"applies to this overlay
  plane and the plane beneath it"* — the plane beneath an overlay plane
  is the desktop — and the requirements put a virtual plane at the
  bottom-most z order filled with black, leaving the desktop as the
  bottom-most *real* plane. **Overlay planes are composited above the
  desktop plane.**

- **The one non-Microsoft source points the same way — and it is a
  mailing-list comment, not driver documentation.** In the August 2021
  amd-gfx thread *"drm/amd/display: Fix two cursor duplication when
  using overlay"*, an AMD engineer writes that on Windows *"the cursor
  is drawn on the 'primary plane' instead of on top of the primary and
  overlay"* — the cursor rides the primary plane, i.e. **below** any
  overlay plane, which is consistent with an overlay plane covering it.

  **Bottom line: no Microsoft document places the hardware cursor plane
  in the overlay z-order.** An earlier version of this document
  asserted the opposite ("the hardware cursor is its own plane,
  distinct from plane 0"); that assertion is **withdrawn**. MPO remains
  a strong *correlate* of cursor-invisibility reports — disabling it is
  the most effective known mitigation — but the scanout mechanism is
  **not** established, and on NVIDIA hardware the pointer is generally
  understood to be composited at the end of the scanout chain.

- **DWM decides which surfaces become overlay planes, and the decision
  is frame-rate based.** `OverlayMinFPS` is the *only* overlay-related
  registry value Microsoft documents **on that page**
  (→ <https://learn.microsoft.com/en-us/windows/win32/dwm/registry-values>):

  > A DWORD. If this value is present and set to zero, the Desktop
  > Window Manager disables its minimum frame rate requirement for
  > assigning DirectX swap chains to overlay planes in hardware that
  > supports overlays. This makes it more likely that a low frame rate
  > swap chain will get assigned and stay assigned to an overlay plane,
  > if available. This mechanism was introduced in its present form in
  > Windows 11.

  The word that matters is **"stay"**. See
> [`OverlayMinFPS` set to zero — the right knob for one symptom, the wrong
> knob for another](#overlayminfps-set-to-zero--the-right-knob-for-one-symptom-the-wrong-knob-for-another)
> for why that makes it the right knob for one symptom and the wrong
> knob for another.

Putting those together: DWM promotes a DirectX swap chain to a
hardware overlay plane; that plane is blended in z-order; *if* the
hardware cursor is a separate plane ordered **below** it, the cursor
sits under the overlay and is not visible. Whether a given swap chain
is promoted is a **per-swap-chain, per-moment** decision — and a swap
chain is promoted **as a whole rectangle**, never in part.

**Two limits on that reasoning, both of which matter here.**

1. **Per-output, not per-adapter.** A plane only exists on an output
   whose hardware has one. The primary DisplayPort output here reports
   `MPO Caps: Not Supported` in the **current display configuration**,
   so **no overlay plane is reported on the primary display at all** —
   as currently reported. (Those figures are mode- and
   configuration-dependent, so a refresh-rate or mode change, a clone
   configuration, or unplugging the second display could change them.)
2. **Whole rectangles only — for a promoted surface that is the whole
   window.** A promoted swap chain occupies **one rectangle** on its
   plane: `DXGK_MULTIPLANE_OVERLAY_ATTRIBUTES` carries a single
   `SrcRect` / `DstRect` / `ClipRect` set, `DstRect` is "relative to the
   monitor resolution", and "Outside of the destination rectangle, each
   layer must be treated as transparent (0,0,0,0)". So a single plane
   cannot itself be split into "cursor visible here, invisible there".
   DWM's desktop swap chain is per *output*.
   **But this does not rule out a promoted *sub-rectangle***: nothing in
   the DDI documentation says a window's sub-rectangle can never be
   presented as its own surface, and this document's own §3 census
   lists exactly those surfaces — `FileExplorer_FolderViewIsland` →
   `Microsoft.UI.Content.DesktopChildSiteBridge` →
   `InputSiteWindowClass`, all `WS_EX_NOREDIRECTIONBITMAP` — each a
   sub-rectangle of the Explorer window with its own swap chain. Any
   promoted rectangle in monitor space hides the cursor where it
   overlaps, so the DirectComposition child-surface case is **not**
   excluded here.

And the "cursor is a separate z-ordered plane" premise is itself
**not documented** (see the `Plane0ForVirtualModeOnly` bullet above, and
the amd-gfx statement that the cursor is drawn on the primary plane).

**Therefore MPO is a leading hypothesis, not a demonstrated one.** It
remains a good candidate *on the secondary output*, where overlay planes
do exist and where the chat app lives; it is **not a candidate as
currently reported** on the primary output, where Explorer and VS Code
live and where the intra-window split is reported. Something else must
cover the primary-display cases — either the cursor plane simply not
being refreshed over accelerated content (mechanism B), or the
application suppressing the cursor there (mechanism C, which has now
been measured on this machine — on the primary, at (901, 660)). On the
primary, **both B and C remain open.**

Note also what the `OverlayMinFPS` wording implies for the fix: the
value gates **how readily** swap chains get assigned to overlay planes.
Setting it to zero makes DWM assign **more** surfaces to overlay
planes. For a bug whose cause is an overlay plane covering the cursor,
that is the wrong direction — see [the fix section](#2-disable-mpo--the-broad-fix-reboot-required-secondary-output-only-as-reported).

### 3. The Explorer window tree — and the limit of what it proves

A static geometric census of the Explorer child-window tree
(`scripts/diagnose_cursor14.py`, plus a parentage/placement probe)
gives, for the two open Explorer windows:

**Downloads window** (minimised) — nav pane *and* file list are both
classic GDI:

| Region | Chain | `WS_EX_NOREDIRECTIONBITMAP` | Class cursor |
|---|---|---|---|
| Navigation pane | `ShellTabWindowClass` → `DUIViewWndClassName` → `DirectUIHWND` → `CtrlNotifySink` → `NamespaceTreeControl` → `SysTreeView32` | no | `NamespaceTreeControl` = **NULL**, `SysTreeView32` = ARROW |
| File list | `CtrlNotifySink` → `SHELLDLL_DefView` → `DirectUIHWND` → `ScrollBar` | no | ARROW |

**Home window** (visible) — same nav pane, but the file list is a
DirectComposition island:

| Region | Chain | `WS_EX_NOREDIRECTIONBITMAP` | Class cursor |
|---|---|---|---|
| Navigation pane | identical to Downloads (`NamespaceTreeControl` → `SysTreeView32`) | no | NULL / ARROW |
| File list | `CtrlNotifySink` → `SHELLDLL_DefView` → `FileExplorer_FolderViewIsland` → `Microsoft.UI.Content.DesktopChildSiteBridge` → `InputSiteWindowClass` | **yes** (ex `0x00200000`) | NULL / NULL |

Both windows also carry a window-level
`Microsoft.UI.Content.DesktopChildSiteBridge` (ex `0x00200000`) and
`InputSiteWindowClass`, plus an `InputNonClientPointerSource`
(ex `0x00280000` = `NOREDIRECTIONBITMAP|LAYERED`) as direct children of
`CabinetWClass`. In the Home window that bridge spans the whole tab
content, covering *both* columns.

**What this does and does not show.** It corrects an earlier claim of
ours. An earlier version of this document asserted "the region that
owns its own DirectComposition surface is where the pointer works" —
that table mixed the two windows: the file-list row was taken from
*Home*, the nav-pane observation from *Downloads*. Corrected, the two
windows have the **same** nav pane (classic GDI in both), and the only
structural difference is the file list. The nav pane is hidden in the
user's report while the neighbouring file list is visible, and in the
Downloads window **both are classic GDI** — so *"own-DComp-surface ⇒
hidden, classic GDI ⇒ visible"* does **not** explain the split. That
rule is withdrawn.

**Where the two Explorer windows are, and why that matters.** The Home
window occupied (557, 52)–(1981, 852) — i.e. the **primary** display,
the one that currently reports no overlay plane. So the clearest
instance of the split (nav pane hidden, file list visible, same window)
happens on a display where mechanism A **cannot operate as currently
reported**. This is the single strongest piece of evidence against
"MPO is the mechanism" — subject to that reading being a property of
the current mode and display configuration rather than of the hardware.

The honest position: the window class does **not** predict which
regions lose the pointer, and there is **no public API** to ask whether
a given surface was promoted to an overlay plane (see
`diagnose_cursor15.py`: no cursor-plane or overlay API is reachable
from user mode; `dwgdi32.dll` is not loadable). The structural census
is therefore supporting context, not a mechanism.

**One more structural lead — now retired.** The nav pane is a
`NamespaceTreeControl` hosting a `SysTreeView32`, and the nav pane is
where the pointer disappears in *both* windows — the one thing those
two windows have in common. That made the nav pane's NULL class cursor
the obvious suspect, and it was this document's best structural lead.
**It is now measured and it is not the answer**: over the nav-pane tree
the OS reported a real, showing cursor (a hand, `0x1001F`), and the
shell's tree view sets that cursor itself (§3b). The structural lead is
withdrawn; what the nav pane shows is a **display-path** fault, in the
same window and on the same display where the neighbouring file list
draws its arrow perfectly.

**A note on the class names.** Everything in the two tables above is an
**observation from this machine** (`scripts/diagnose_cursor14.py`),
re-verified today. It is **not** a documented contract: the Learn
documentation has no pages for `DirectUIHWND`, `DUIViewWndClassName`,
`CtrlNotifySink` or `ShellTabWindowClass` (the old DirectUI reference
section now 404s), and `NamespaceTreeControl` on learn.microsoft.com
denotes the COM interface `INamespaceTreeControl`, **not** a window
class. Read the tables as a census of this build, not as an API.

### 3b. The nav pane's NULL class cursor — and why it is **not** the explanation

`NamespaceTreeControl` — the root of the Explorer navigation pane — has
`WNDCLASS.hCursor == NULL`. That fact is real and reproducible
(`scripts/diagnose_cursor14.py`). What it does **not** mean was, until
now, mis-stated in this document.

**First, the quotation.** MSDN's current *WNDCLASS.hCursor* wording is:

> This member must be a handle to a cursor resource. If this member is
> NULL, an application must explicitly set the cursor shape whenever the
> mouse moves into the application's window.

(An earlier revision of this document quoted an older sentence — "the
system has no cursor for the class, and the cursor does not appear when
the mouse is over a window of this class" — which is no longer the text
on that page.)

**Second, and decisively: a NULL class cursor does not hide the
pointer.** Two primary sources say so.

- *Setting the cursor image* (MSDN), describing `DefWindowProc`'s
  `WM_SETCURSOR` fallback: *"1. If the window has a parent, forward the
  WM_SETCURSOR message to the parent to handle. 2. Otherwise, if the
  window has a class cursor, set the cursor to the class cursor.
  3. If there is no class cursor, set the cursor to the arrow cursor."*
  Step 3 sets the **arrow** — a visible cursor.
- Raymond Chen, *"When I define a window class with no default cursor,
  what is the explanation for the cursors that appear in my client
  area?"* (Old New Thing, 2025-04-24): *"If there is no registered
  class cursor (if you set it to nullptr), then that final fallback step
  doesn't do anything… If you fail to fulfill that obligation, then
  nobody sets the cursor, and you get leftover garbage."*

So a NULL class cursor **removes the system's fallback and obliges the
app to set the cursor itself**. The observable is either the app's own
cursor or the *previous* cursor persisting — never a suppressed one.
A NULL class cursor is the **precondition** for an app-set cursor, not
a suppression mechanism. `SetCursor(NULL)`'s own remarks say to set the
class cursor to NULL precisely *because* the app is about to take over
— the opposite of the reading this document used to give it.

**Third, the nav pane has been measured, and it is not C.** A probe
over the nav-pane tree at (581, 302) returned

```
hCursor = 0x1001F   (HAND - the pointing hand - a real handle)
flags   = 0x00000001 (CURSOR_SHOWING set, CURSOR_SUPPRESSED clear)
```

and over the neighbouring file list at (982, 478) `0x10003`
(`IDC_ARROW`) with the same flags. The OS **had** a cursor and reported
it as showing — so over the nav pane the fault is in the **display
path**. The nav pane is on the **primary**, where **A** is
capability-excluded (§3), so that leaves **B** as the only remaining
display-path mechanism. (This reading moved nothing: `cursor_shapes.py`
is a pure `GetCursorInfo` read at the pointer's current position. The
one synthetic input in the investigation is the one-pixel `SetCursorPos`
nudge in `cursor_is_composited.py`, a different probe — see §Verdict.)

The handle also settles the "what is the shell doing?" question: the
tree view **handles `WM_SETCURSOR` and sets a hand of its own** — it
does *not* call `SetCursor(NULL)`, and it does *not* use the ARROW its
class declares. The NULL class cursor on `NamespaceTreeControl` is
**doubly inert** here: the window under the mouse over the tree is
`SysTreeView32`, and the shell's tree view sets the cursor itself
anyway.

**What the two variants of C have in common.** `SetCursor(NULL)` from
a `WM_SETCURSOR` handler, and CSS `cursor: none` in a Chromium/Electron
app, are two different *causes*, but they are **one observable**: the OS
has no cursor for the window under the pointer. `GetCursorInfo` cannot
tell them apart — it does not need to. The point of the probe is to
separate **C** from **A/B**, and it does that with one API call:

| `GetCursorInfo` at the pointer | Verdict |
|---|---|
| `flags == 0x00000000` (`CURSOR_SHOWING` clear) | **Mechanism C.** The app asked for no cursor. Nothing is covering it; there is nothing to display. (A NULL `hCursor` corroborates it, but key on the **flag** — `CURSORINFO.hCursor` is documented only as "a handle to the cursor", with no guarantee it is NULL when the cursor is hidden.) |
| `CURSOR_SHOWING` set, a real handle | **C is excluded** for that surface. The OS had a cursor to draw, so the fault is in the display path. Over the nav pane that is **B**: **A** is capability-excluded on the primary (§3). |

`scripts/cursor_shapes.py` runs exactly that read, names the cursor
shape when there is one, and prints `MECHANISM C` or `not C` per
surface in watch mode. It is **not** a by-eye test and it is the only
test in this document that can be run without looking at the screen.

**How to run it.** `python scripts\watch_cursor5.py 120` — sweep the
pointer over the **navigation pane** (the tree), then over the **file
list** beside it, then over the strip of the nav pane that is *not* the
tree. Its summary prints, per distinct chain: the **class cursor of
every ancestor** (`class cursors:`), the classes in the chain whose
class cursor is **NULL** (`NULL-CURSOR classes in chain:`), an
`EXPLORER` / `NAVPANE` tag, the display, and the live cursor with its
flags.

**Which discriminator applies depends on the app.** Explorer is a
native Win32 app: CSS cannot apply to it, so the only C variant
possible there is a `WM_SETCURSOR` handler calling `SetCursor(NULL)`.
In a **Chromium/Electron** app the OS-level cursor is driven per
region by the app from CSS, so CSS `cursor: none` and a
`WM_SETCURSOR`/`SetCursor(NULL)` handler are *the same act* at the OS
level and `GetCursorInfo` cannot tell them apart; the only user-side
separator there is the app's own developer tools (VS Code: Help → Toggle
Developer Tools; a browser: F12) — point at the region, select the
element in the Elements panel and read the `cursor` property in the
Computed styles: `none` is the CSS variant.

**The `WM_SETCURSOR` ordering is what makes this sharp**:

1. When the mouse moves, the system sends `WM_SETCURSOR` to the
   **window under the mouse**; if that window does not handle it, the
   message travels up the **parent chain**.
2. Only if nobody handles it does the system fall back to the
   **window class's** cursor (`WNDCLASS.hCursor`) — and then to the
   arrow.
3. `SetCursor(NULL)` from a `WM_SETCURSOR` handler is the documented
   way to *remove* the cursor for a region — MSDN: *"If this parameter
   is NULL, the cursor is removed from the screen."*

### 4. It is not a per-monitor DPI problem

The split is *inside* one window on one display, at 96 DPI, with
`GetDpiForMonitor` reporting 96 for both panels.

## What was ruled out

| Candidate | Verdict | Evidence |
|---|---|---|
| Blank / tampered cursor file | ruled out | `aero_arrow.cur` SHA256 identical to the pristine `WinSxS` copy; every scheme file has non-zero visible pixels |
| Custom cursor scheme | ruled out | active scheme is stock "Windows Default", `Scheme Source = 2`, all `aero_*.cur` |
| Low contrast against the background | ruled out | the live arrow has 76 **fully opaque** light pixels and 67 partially-transparent dark ones (anti-aliased outline + soft shadow, 2-3 px wide) — no background can hide it (§2b) |
| Pointer size / shadow as a fix | ruled out as a *fix* | for the same reason; a shadow cannot help against occlusion. It remains a 10-second *test* of the mechanism, not a remedy |
| "Hide pointer while typing" | ruled out | `SPI_GETMOUSEVANISH = 0`; no `MouseVanish` value |
| Pointer trails | ruled out as the trigger | `HKCU\Control Panel\Mouse\MouseTrails = 0` — trails are **off** on this machine |
| Stuck modifier / mouse button | ruled out | `GetAsyncKeyState` high bits clear for every modifier and button |
| Accessibility (StickyKeys, MouseKeys, HighContrast…) | ruled out | `StickyKeys` 0x510, `ToggleKeys` 0x62, `MouseKeys` 0x62, `HighContrast` 0x126 — all stock defaults |
| App calling `SetCursor(NULL)` | **NOT ruled out — confirmed on one surface** | `CURSOR_SUPPRESSED` was never set in 8296 samples, but that flag means *touch/pen* input, not hiding. A targeted probe found `flags == 0x00000000` (and a NULL `hCursor`) over a Chromium render widget, stably over 2956 samples (§1) |
| NULL window-class cursor as the cause | **ruled out** | it does not hide the pointer. `DefWindowProc`'s `WM_SETCURSOR` fallback ends at *"If there is no class cursor, set the cursor to the arrow cursor"*, and Raymond Chen (Old New Thing, 2025-04-24): *"nobody sets the cursor, and you get leftover garbage."* A NULL class cursor removes the fallback and obliges the app to set the cursor itself (§3b) |
| Third-party mouse utility / driver | ruled out | only the stock `msmouse.inf` / `mouclass` stack is loaded; no Razer / Logitech / SteelSeries software running |
| Per-monitor DPI / scaling | ruled out | the split is inside one window on one display |
| HDR | not applicable | both panels are 1920×1080 office monitors (MSI B27T-7, HP P221) with no HDR support |
| TDR / display resets | ruled out | no `4101` display-reset events in 30 days |
| Driver age | **not** ruled out — but low-yield | the installed driver is **576.02** (build 32.0.15.7602, `DriverDate` `12/04/2025`) and NVIDIA's own lookup lists **621.01 (21 Oct 2026)** as the current WHQL Game Ready driver for this GPU, so 576.02 is not current. But parsing the release notes from 565.90 to 621.01 turns up **no** mention of cursor, hardware cursor, DirectComposition or HAGS — a driver update is a **shot in the dark**, not an evidence-based fix (see fix step 5) |
| `cua-driver` / `codex-computer-use` / `windows-mcp` hiding it | ruled out | none of the three imports `ShowCursor`, `SetSystemCursor`, `SetCursor` or `SetWindowsHookEx`; the `windows-mcp` hooks only swallow input while an AI lease is active, and no lease is active |
| The `Cua.AgentCursorOverlay` | ruled out | it sits below every foreground app in z-order, and the pointer was demonstrably visible and on the hardware plane while the overlay was present |
| The OpenAI Codex / "Owl" desktop app | **not** ruled out for the chat surface | the app **is** running (see below) and ships a gated `cursor: none !important` injector; it is the one candidate with a *different* fix, and it needs a direct test |
| **MPO as the mechanism on the primary display** | **ruled out — capability, not likelihood** | the primary output reports **zero** overlay planes (`MaxPlanes=1`, per-source caps `0x00000A00`, dxdiag "Not Supported"); `IDXGIOutput2::SupportsOverlays` is **FALSE for both outputs**; the documented overlay z-order has **no cursor layer**. A symptom on the primary therefore cannot be an overlay plane covering the cursor |
| A thread-local / `ShowCursor`-based hide | ruled out here, but the discriminator is not absolute | Chromium's `kHideCursorWhileTyping` hides via a `kNone` platform cursor, not `::ShowCursor`, and `ShowCursor`'s counter is per-thread while `CURSORINFO` is global. Excluded on this machine by its own gate: mouse-vanish is **off**, a character key-down in a text input is required, and the affected surfaces are not text inputs (§Verdict) |

### The Codex "Owl" `cursor: none` injector — real, and it needs a direct test

A byte scan of the installed Codex bundle
(`OpenAI.Codex_26.1007.9446.0_x64__2p2nqsd0c76g0\app\resources\app.asar`)
finds a genuine document-wide cursor suppressor at offset **6816474**:

```js
function ac(e, t, n = !1, r) {
  let i = `html, body, body *, #${t}, #${t} *`,
      o = `${i} { cursor: none !important; ... }`;
  // creates/updates a <style> element carrying that rule
}
```

It injects `cursor: none !important` over the entire document.

> **A correction to an earlier version of this paragraph**, which said
> the bundle "ships `.annotation-selection-cursor-bubble` / `-beam`
> rules (`position: fixed; z-index: 1`) that draw the app's own pointer
> in its place". A byte-level pass over the extracted CSS says
> otherwise:
>
> | Selector | Offset | What it actually declares |
> |---|---|---|
> | `.markers-layer` | 7182528 | `position: fixed; inset: 0; z-index: 1; pointer-events: none` — the **overlay layer**, not a pointer |
> | `.marker` | 7182016 | the hiding rule: `cursor: none` |
> | `[data-browser-comment-annotation-selection-modifier-active] .marker` | 7182597 | the same `cursor: none`, scoped to the active annotation-selection modifier |
> | `.annotation-selection-cursor-bubble` | 7181204 | a **transform** transition — it *moves*, it does not draw a pointer |
> | `.annotation-selection-cursor-beam` | 7181376 | an **opacity** transition — it fades, it does not draw a pointer |
> | `._CommentMode_1gipt_9` | 462105019 | the app's own pointer: `cursor: url("data:image/svg+xml,…") 4 20, crosshair` — a **real, non-NULL** custom OS cursor, not a CSS `none` |
>
> So the `position: fixed; z-index: 1` belongs to `.markers-layer`, and
> the bubble and the beam are transition rules. The app's own pointer
> is a genuine custom cursor, which is the important part: it means the
> app can show a pointer **without** the OS pointer being visible — so
> the injector, when it fires, is invisible to `GetCursorInfo` unless
> the flags are read.

Two consequences worth stating plainly. First, the `.marker` rules at
7182016 and 7182597 are a **second, independent** `cursor: none` path —
scoped to the annotation markers rather than the whole document, and
they do not go through the `ac` injector at all. Second, the app's own
pointer is a real OS cursor, so on a surface where the injector *is*
engaged the user may see the app's crosshair even though the OS arrow
is gone — which is exactly the "I can see a pointer but not my mouse"
confusion the symptom report describes.

**The app is running.** The package `OpenAI.Codex_26.1007.9446.0`
ships an executable called `ChatGPT.exe` (nine processes, with
`--owl-scoped-user-agent-prefix=CodexBrowser` and an
`OwlElectron_NotifyIconHostWindow`), and its main window
(`Chrome_WidgetWin_1`, title `ChatGPT`) was minimised at the time of
measurement. So a name-based search for "Codex"/"Owl" processes misses
it — `ChatGPT.exe` is the one to look for.

Two things follow:

1. The injector is **gated, but not as narrowly as first stated.** An
   earlier version of this document said the injector "only applies it
   when the browser-comment annotation-selection cursor element is
   present in the shadow root". A follow-up pass over the bundle found
   **five** call sites of `ac` (the function is defined at **6816349**),
   and only **one** of them is gated on that element:

   | Call site (asar offset) | Gate |
   |---|---|
   | `6817596` | the **annotation-selection cursor element** is present in the shadow root |
   | `7206321` | a **pointer drag** is live, or there is a native text selection; injects into the **host page's** document, not the app's own |
   | `7207240` | `Dx` — the only *definition* that calls `ac`; gated on **comment interaction mode** |
   | `7220118` | annotation **target mode** is `create` **and** the anchor is **not** a text selection |
   | `7225741` | **comment interaction mode**, plus the annotation target-mode and anchor guards |

   Two of the five (`7207240`, `7225741`) are gated on **comment
   interaction mode**, which is a **broader** trigger than "the
   annotation cursor element is present" — it does not require the app's
   custom pointer bubble to be showing. The `7206321` site is the
   widest of all: it is gated only on a drag or a text selection, and it
   targets the **host page's** document, so a comment interaction
   inside the embedded browser can suppress the pointer over the page
   the user is looking at.

   > **One offset from an earlier draft is deliberately absent.**
   > `7223745` was listed as a call site of `ac`, but it is a call to
   > **`Dx`** inside a `useLayoutEffect` — it reaches the injector
   > *through* `Dx`, and listing both double-counts the same path.

   The byte-level fact itself is confirmed: `cursor: none !important`
   occurs **exactly once** in the 567 MB asar, at absolute offset
   **6816474**.
2. If it *is* engaged, Chromium will hide the OS cursor over the whole
   app — and `GetCursorInfo` will report a **NULL `hCursor` with
   `CURSOR_SHOWING` clear**. That is an **objective, one-sample
   test** — and it separates the candidate mechanisms cleanly:

   | `GetCursorInfo` over the ChatGPT window | Conclusion |
   |---|---|
   | `hCursor == NULL`, `CURSOR_SHOWING` clear | the app suppressed the cursor for that region — the app's own injector, an app-level cause with an app-level fix (**mechanism C**) |
   | `CURSOR_SHOWING` set, real cursor handle, glyph not in the framebuffer | the same hardware-cursor-plane occlusion as everywhere else (**mechanism A/B**) |

   > **A correction to an earlier version of this table**, which said
   > the injector would show up as "`CURSOR_SUPPRESSED` (0x2) with a
   > NULL `hCursor`". `CURSOR_SUPPRESSED` does **not** mean "the
   > cursor is hidden" — it means the pointer is being driven by
   > **touch or pen** input. Hiding shows up as a NULL handle with
   > `CURSOR_SHOWING` clear. Conflating the two would have made this
   > test read the wrong flag.

   Until that sample is taken, the chat-surface symptom has two possible
   causes and they have different fixes. `scripts/cursor_shapes.py`
   records the flags and the cursor shape for every window chain it
   sees, and prints a `MECHANISM C` / `not C` verdict for each, so it
   settles the question the moment the pointer visits that window.

## Microsoft has already fixed it upstream

**Windows 11 Insider Experimental Preview Build 26340.9233**
(KB5124042, released **21 August 2026**), 26H2 branch, release notes:

> ### [Input]
> - Fixed an issue in the latest builds causing the mouse cursor not to
>   be visible in certain apps.

Source: [Windows 11 Insider Experimental Preview Build 26340.9233](https://learn.microsoft.com/en-us/windows-insider/release-notes/experimental/preview-build-26340-9233) ·
[blog post, 21 Aug 2026](https://blogs.windows.com/windows-insider/2026/08/21/announcing-new-builds-for-21-august-2026/)

The same `[Input]` fix is listed in the release notes of two further
Experimental builds on the same branch — **28120.2760** and
**29661.1000** — so it is a carried-forward branch fix, not a one-build
entry.

**It is not in the build you are running.** This machine is on
**26300.9711** — the 26H2 General Availability Channel build released
**2026-10-13** ([KB5127648](https://support.microsoft.com/help/5127648)),
which is the *newest* 26H2 GA cumulative update as of 2026-10-22. Its
release notes contain no cursor, pointer, display or graphics fix (the
only mouse entry is a gaming mouse-input stutter fix). The fix is in
the Insider **Experimental** channel (build 26340.x), built "based on
Windows 11, version 26H2 via an enablement package".

So:

- **Windows Update will not fix it today** — you are already current on
  the GA branch.
- The fix is available **now** if you enrol in the Windows Insider
  Program and take the 26H2 Experimental channel (26340.x).
- Otherwise it should reach the 26300 GA branch in a later monthly
  cumulative update.

Checked and cleared: every GA cumulative update for 26H2, 26H1 and
25H2 — KB5127648, KB5124010, KB5127646, KB5124006, KB5124012,
KB5129194, KB5120996, KB5121000, KB5124008, KB5120998, KB5121003,
KB5121767, KB5129195, KB5101650, KB5101684, KB5101681, KB5101649 —
contains **no** invisible-cursor fix. The nearest thing in the GA
channel is
[KB5124008](https://support.microsoft.com/help/5124008)
(2026-09-08) — but note it is **not** a 26H2 cumulative update: it
applies to **25H2** (build 26200.9445) and **24H2** (build 26100.9445)
only. Its entry, in full:

> [Mouse] Fixed: This update addresses an issue that prevented
> customized mouse cursor settings, including pointer style and color,
> from displaying correctly. Selected cursor options and colors now
> work as expected.

That is a **pointer-style/colour personalization** bug — a different
thing, and this machine is on the stock scheme anyway. The 26H2
branch's own mouse entry (in KB5127648) is a gaming mouse-input stutter
fix.

> **A correction to an earlier version of this section**, which called
> KB5124008 *"the only cursor-related GA entry for the 26H2 branch"*
> and quoted only its first sentence. Both halves were wrong: it is a
> **25H2/24H2** update, and the quote ended mid-sentence, before
> *"Selected cursor options and colors now work as expected."*

> **A correction to an earlier version of this section**, which said
> "the only cursor-related GA entry anywhere". That is **too strong**.
> The GA 25H2 / 24H2 branches carry *other*, unrelated cursor fixes —
> **KB5120998** (pointer) and **KB5101684 / KB5120996** (cursor-size
> persistence) among them. What is true is the narrow claim: **no GA
> cumulative update on any branch contains the invisible-cursor fix**;
> the ones that exist are about pointer style, pointer size and
> persistence.

## The app vendors agree: it is not an app bug

**VS Code issue microsoft/vscode#239110**, *"Activating display pointer
trails makes text editing pointer disappear"*, was closed as **not
planned** by Alexandru Dima on **2026-08-27**:

> This is a known Windows 11 24H2 bug that affects cursor rendering in
> Chromium-based applications (including VS Code's Electron shell) […]
> Since the root cause is in Windows / Chromium rather than VS Code,
> there isn't a fix we can apply on our side.

The workarounds listed there (change the Windows cursor scheme, raise the
pointer size to 2+, disable pointer trails) are the reporter's, and per
§2b they cannot address this mechanism. Pointers trails are **off** on
this machine anyway (`MouseTrails = 0`).

Also relevant: VS Code #217357 *"Mouse cursor disappears in editable
windows"* (closed by Microsoft as *not a VS Code bug*),
VS Code #204103 and Chromium #40239916 (labelled `chromium` / `gpu` /
`upstream`, *mitigated*; *"mostly affects users with AMD graphics cards,
but also NVIDIA and Intel"*, *"not limited to VS Code but affects other
Chromium-based applications"*), and Neowin's *"Microsoft, AMD, Nvidia,
are all sleeping on Windows 11 MPO display issues"*.

## The hardware vendor recognises the symptom too

**[NVIDIA KB 5157](https://nvidia.custhelp.com/app/answers/detail/a_id/5157/~/what-is-multi-plane-overlay-%28mpo%29-in-windows-11)**
is NVIDIA's own MPO article. It was originally published on
**2021-09-29** under the title *"After updating to NVIDIA Game Ready
Driver 461.09 or newer, some desktop apps may **flicker or stutter when
resizing the window** on some PC configurations"*, and has since been
retitled *"What is Multi-Plane Overlay (MPO) in Windows 11"*. It
publishes two registry files in its attachments: `mpo_disable.reg`
(252 bytes) and `mpo_restore.reg` (226 bytes). The former writes

```
HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm
    "OverlayTestMode" = dword:00000005
```

and the latter **deletes** the value:

```
HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm
    "OverlayTestMode" = -        (value removed, key left in place)
```

> **A correction to an earlier version of this section**, which said
> `mpo_restore.reg` "sets the same value back to `00000000`". It does
> not: it **deletes** the value, which is why the undo block in the
> fixes section uses `reg delete` and why `-Action Status` afterwards
> reports `OverlayTestMode: not set (Windows default)` rather than
> `0`.

The live article's own instruction for **both** files is *"Restart your
PC to apply the changes"* — a restart, not a power cycle. The power
cycle in this document comes from the NVIDIA GeForce Forums thread
that reports the *cursor* specifically, and it is the more reliable of
the two (see below).

The article also carries a newer note that matters for anyone who
re-checks the configuration later:

> Starting with Release 610 drivers, MPO is now supported on all
> monitors in multi-monitor configurations on Blackwell GPUs,
> improving performance and power efficiency with multiple displays.
> For optimal functionality, update Windows to at least build
> 26100.7705 or 26200.7705.

That is about **Blackwell / R610**, not about this machine's RTX 2060,
but it is the reason to re-read the per-output MPO numbers rather than
trust a measurement taken once: the driver and the Windows build can
both change what the numbers mean.

**Note what NVIDIA's own framing is: flicker and stutter.** That is
the symptom class the KB was written for, and it is the same class
that `OverlayMinFPS = 0` targets (see
[the fix section](#overlayminfps-set-to-zero--the-right-knob-for-one-symptom-the-wrong-knob-for-another)).
The *steady, invisible* cursor is a different symptom class, and
NVIDIA's article does not claim to address it.

The **cursor-specific** report is an NVIDIA GeForce Forums thread,
*"Cursor glitching/disappearing after NVIDIA driver update"*
([nvidia.com/forums/…/569227](https://www.nvidia.com/en-us/geforce/forums/game-ready-drivers/13/569227/cursor-glitchingdisappearing-after-nvidia-driver-u/)),
on the same hardware generation and the same OS: **Windows 11 24H2,
GeForce RTX 2060 Super, the cursor disappearing and glitching** after a
driver update (566.36 → 572.70), and — like the Chromium apps here —
appearing mostly in Chrome/Edge. The remedy discussed in that thread
is `mpo_disable.reg` from KB 5157, and the **full power cycle**
(`Win+X → Shutdown → Shutdown`, then power back on, *not* a restart)
is specified in the thread — note that the KB itself says only
*"Restart your PC to apply the changes"* (the live article's wording;
the 2021 original said *"reboot your PC to complete the changes"*);
the power-cycle instruction comes from the thread, and is the more
reliable of the two. The thread is explicit about the difference: the
NVIDIA staffer's reply spells out `[win]+[x] >> Shutdown >> Shutdown /
Power back on` — **twice**, once to disable and once to re-enable.

That is the same registry value and the same mechanism as step 1 of the
fix above, on a card in the same class as this one, for the same
symptom. It is a **workaround from the GPU vendor**, not a proof of
mechanism — and per the per-output argument above it can only take
effect on the **secondary (HDMI)** output on this machine, as currently
reported.

## What is still open

One question is **settled** and changed the shape of the others:
**mechanism C is real and measured on this machine** (§Verdict). What
remains open is **which mechanism applies to *which* surface** — the
symptom is not one bug, and the fixes differ per mechanism. Three
items, worth keeping separate from what is proven:

1. **Which mechanism applies to the primary display's remaining
   surfaces.** For the surfaces where the probe reports a *real*,
   shown cursor, C is excluded, and the choice is between A and B.
   Mechanism A (overlay plane) is **not a candidate as currently
   reported** on the primary — that output reports
   `MPO Caps: Not Supported` — and the symptom is intra-window, which
   a whole-window overlay plane cannot produce. So on the primary the
   remaining **display-path** candidate is **mechanism B: the hardware
   cursor plane is not refreshed over the affected region** — but
   mechanism **C** is still live there too (item 2, and the VS Code tree
   is a Chromium render widget), so the cursor-state probe must be run
   over the primary's surfaces before B can be treated as the answer.
   B is consistent with the VS Code maintainer's own diagnosis — *"a
   known Windows 11 24H2 bug that affects cursor rendering in
   Chromium-based applications"* — and with Microsoft's own fix wording,
   *"not to be visible in **certain apps**"*. What is still missing is a
   measurement that *separates* A from B on the one surface where both
   could apply (the secondary output).

2. **Why the Explorer nav pane and the neighbouring file list behave
   differently.** This used to read "is the nav pane mechanism C?" — and
   that question is now **closed: it is not C.** A probe over the nav
   pane tree at (581, 302) returned a real, showing **hand** cursor
   (`hCursor = 0x1001F`, `flags = 0x00000001`), and the neighbouring file
   list at (982, 478) returned `0x10003` (`IDC_ARROW`) with the same
   flags (§Verdict, §3b). The OS had a cursor to draw in both places.

   So the nav pane is a **display-path** fault — and it is on the
   **primary**, where **A** is capability-excluded (§3), so that leaves
   **B** — and the
   structural lead that used to sit here — the NULL class cursor on
   `NamespaceTreeControl` — is **retired**: a NULL class cursor removes
   the system's arrow fallback and obliges the app to set the cursor
   itself; it does not suppress anything (§3b). The shell's tree view
   sets a hand cursor of its own, which the measurement confirms.

   What is left is not an API question but a display question: **why
   does the display path show the pointer over one region of the same
   window and not the other?** That needs the by-eye tests — 0a
   (trails), 0c (move the window to the other display), 0d
   (`--disable-gpu`), 0e (force a repaint) — on the nav pane, not more
   `GetCursorInfo` reading.

3. **Whether the Codex/ChatGPT app's own injector is engaged** for the
   chat surface (see above), and which of the **three** C-variants
   applies at any given point: `SetCursor(NULL)` from a `WM_SETCURSOR`
   handler, CSS `cursor: none` (which is what a Chromium app normally
   does — it drives the cursor per region from CSS, and its
   render-widget class cursors are both NULL), or an injected
   stylesheet.

   > **A fourth candidate is no longer on the list.** Earlier drafts
   > counted "a NULL window-class cursor" as a C-variant. It is
   > **ruled out**: a NULL class cursor does not suppress the pointer
   > (§3b, and the "What was ruled out" table) — it only removes the
   > system's arrow fallback.

   **Partial answer, already in hand — but unattributed.** A sample
   taken over a Chromium chain
   (`Chrome_WidgetWin_1 > Chrome_RenderWidgetHostHWND`) on the
   secondary display reported

   ```
   live = 0x10005   flags = 0x00000001   (SHOWING=1  SUPPRESSED=0)
   ```

   — a **real, non-NULL I-beam handle**, shown. At that instant the
   app had **not** suppressed the cursor. (Note the flag semantics:
   `CURSOR_SUPPRESSED` means the pointer is being driven by **touch or
   pen** input — it does **not** mean "hidden". Hiding shows up as a
   NULL handle with `CURSOR_SHOWING` clear, a different signal
   entirely.)

   Two caveats the reader should have. The chain is **not** unique to
   the chat app — Brave, VS Code and the ChatGPT app all report
   `Chrome_WidgetWin_1 > Chrome_RenderWidgetHostHWND` — and this
   document records **no pid** for this sample, so as written it cannot
   be attributed to the chat app at all. Give the pid and the run that
   produced it, or state plainly that the sample is unattributed and
   must be retaken over a sent message.

4. **Where the hardware cursor sits relative to the overlay planes, and
   whether Microsoft's Insider fix is *this* bug.** Two things are simply
   not documented, and both matter:
   - **The cursor's plane identity and z-position.** No Microsoft
     document places the hardware cursor in the overlay z-order, and the
     documented `DXGK_MULTIPLANE_OVERLAY_PLANE.LayerIndex` list has
     **no cursor layer**. The only documented cursor↔plane ties are
     about scaling and blending (`Plane0ForVirtualModeOnly`,
     `CursorScaledWithMultiPlaneOverlayPlane0`,
     `CursorDoesNotSupportXorBlendWithMultiPlaneOverlay`,
     `DXGI_HARDWARE_COMPOSITION_SUPPORT_FLAG_CURSOR_STRETCHED`). So
     mechanism A can be **neither confirmed nor refuted** by the
     documentation — it is a driver/GPU decision.
   - **Whether the Insider `[Input]` fix is this symptom.** Its wording
     is *"the mouse cursor not to be visible in **certain apps**"*, which
     describes an app-dependent symptom — but it does **not** describe
     an intra-window split, and it is documented only in Insider
     Experimental builds (26340.9233 / 28120.2760 / 29661.1000) and in
     no GA cumulative update. Whether the GA branches will pick it up,
     and whether it fixes *this* pattern, is unknown.

   Neither of these is settled by running a probe; both are settled by
   **trying the build** (fix step 1) and by **watching what the display
   path does** (PresentMon, fix step 2).

None of this changes the headline verdict, but it changes **which fix
applies to which surface** — and (1) is the one that decides whether
disabling MPO is worth doing at all.

**The first three are answered by the same measurement, and it needs two
minutes of mouse movement, not more analysis.** The four-hour
`watch_cursor5.py` run has finished: **695 samples over 9 distinct
window chains** (Windows Terminal/CASCADIA 339; Brave ×5 groups
140+108+32+3+1; the taskbar 52; Hermes ×2 groups 19+1 — 339+284+52+20
= 695), and the live cursor was a real handle with `CURSOR_SHOWING=1`
in **every single sample**. But the pointer **never visited the
Explorer navigation pane** during that run, so question 2 is still open
— a surface the pointer did not visit is not a negative result, it is
an absence of data. The two-hour `cursor_shapes.py` census has also now
finished: **3919 samples over 22 chains**, which found mechanism C at
**48 samples in Brave** and a real cursor handle everywhere else — and
like the earlier run, it **never visited the Explorer nav pane**, the
Windows Search list, the VS Code tree, or a sent message.

**Run the census yourself** — it is one command and it needs about two
minutes of mouse movement, not two hours:

```powershell
# from the repository root
python scripts\cursor_shapes.py --watch 120
```

The census is printed at the **end** of the run (or on **Ctrl+C**), and
each row carries a **per-surface verdict** — `MECHANISM C` or `not C` —
for every (process, window chain, display, cursor state) combination
the pointer visited. `cursor_shapes.py` writes no log file; if you
want one, redirect the output yourself:

```powershell
python scripts\cursor_shapes.py --watch 120 > %TEMP%\cs2.txt
```

Move the pointer over

- the **Explorer navigation pane** (the left column) and then the
  **file list** beside it, and
- the **Windows Search** results list, and
- the **VS Code** file-explorer tree, and
- the **ChatGPT / Codex app's chat area** (a sent message),

and the census answers 2 and 3 directly, and narrows 1 to the
surfaces where the OS did have a cursor.

## The fixes, cheapest first

> **Before you start: take the agent cursor overlay out of the picture.**
> The `cua-driver` daemon on this machine draws a **full-desktop
> `WS_EX_LAYERED` overlay window**, and it is present **at every logon**
> (the `cua-driver-serve` scheduled task starts the daemon *without*
> `--no-overlay`). It does not cause the missing pointer, but it does
> change DWM's composition path for the whole desktop — which is the
> very thing that decides whether a surface is presented as
> `Composed: Flip` or `Hardware Composed: Independent Flip`, i.e.
> whether an overlay plane is involved at all. So the PresentMon check
> in step 2 is reading a machine that is not clean. Start the daemon
> with `--no-overlay`, or use the driver's `set_agent_cursor_enabled`
> tool, and leave it off for the duration of the experiments. (See
> [Note on `cua-driver`](#note-on-cua-driver).)

**Read this first — it says which of the steps below apply to your
surface.** The probe in test 0 decides it, and the steps are not
interchangeable:

| Probe verdict | What it means | Which steps below apply |
|---|---|---|
| **`MECHANISM C`** | the app asked for no cursor over that region | **None of them.** Steps 1–5 change the display path, and there is no cursor there to reveal. Go to [### 0b](#0b-if-the-probe-says-mechanism-c--what-to-do). |
| **`not C`**, and the symptom is on the **secondary (HDMI)** display | the OS had a cursor and the display path is losing it | Steps **2** (MPO — the secondary output is the one that reports overlay planes), then **3**, **4**, **5**. |
| **`not C`**, and the symptom is on the **primary (DisplayPort)** display | the OS had a cursor; the primary reports `MPO Caps: Not Supported`, so mechanism A is out | Steps **3**, **4**, **5**. Step 2 is unlikely to help here. |

### 0. The by-eye tests — do these FIRST, they cost nothing and they decide the mechanism

> **Why this section exists.** Everything measurable from inside Windows
> has now been measured, and almost none of it can separate the
> candidate mechanisms. The reason is not a shortage of effort — it is
> that **a GDI framebuffer read cannot see the pointer at all.** The
> offset sweep in `scripts/cursor_is_composited.py` measures this
> directly: the arrow's white body scores 0.05–0.07 at **every**
> shifted offset (0.06 at the pointer's exact position), where a
> genuinely composited arrow scores 1.00 (§2). The pointer's pixels are not in
> the image, over GDI surfaces and accelerated Chromium surfaces alike,
> because it lives on the adapter's **hardware cursor plane**. Every
> "the arrow is not in the framebuffer" reading is therefore
> uninformative, and every conclusion drawn from one is unsound.
>
> **Consequence: a plain *still* screenshot of the screen will not
> contain the arrow — it is on the hardware cursor plane, not in the
> framebuffer. Use your eyes, or a phone photo of the screen.** (A
> screen *recording* does contain the pointer, and a
> `Windows.Graphics.Capture` session with `IsCursorCaptureEnabled =
> true` does too — but that last one "will force the system into
> software cursor mode", so it changes the very thing being
> diagnosed.)
>
> **One correction to the above, and it is an important one.** An
> earlier revision of this document concluded from the same facts that
> *"no screen read can help, so only by-eye tests separate the
> mechanisms."* **That was too strong.** The *framebuffer* is blind,
> but the *cursor-state* route is not: `GetCursorInfo` reports whether
> the OS has a cursor at all for the window under the pointer, and
> that single call **decides mechanism C on its own**. Only A-versus-B
> still needs your eyes. That is why test **0** below is not a by-eye
> test, and why it comes first.

Test **0** costs one command and needs no eyes. The five by-eye
tests that follow take a few minutes each and each one eliminates a
mechanism. Do them in order.

| # | Test | How | What it tells you |
|---|---|---|---|
| **0** | **The cursor-state probe** (not a by-eye test) | From the repository root: `python scripts\cursor_shapes.py --watch 120` — then **hover** each affected surface: the Explorer nav pane, the file list, the Windows Search list, the VS Code tree, the chat area. **Or, one surface at a time**, hover the surface and run `python scripts\cursor_shapes.py` with no argument: it prints the pointer's state *right now* and ends with `-> MECHANISM C` if the OS holds no cursor. The one-shot form is the cheapest way to cover all four reported surfaces. | The census prints a verdict per surface. **`MECHANISM C`** ⇒ the application has suppressed the cursor there (`SetCursor(NULL)`, or CSS `cursor: none`) — there is nothing to cover, and **no display fix will help**; the fix is in the app or in the app's CSS. **`not C`** ⇒ the OS had a real cursor to draw, so the fault is in the display path and the by-eye tests below decide A vs B. Run it once with no argument (`python scripts\cursor_shapes.py`) to read the pointer's state and its **shape** at the current position — `ARROW`, `IBEAM`, `WAIT`, `CROSS`, `UPARROW`, `HAND`, `NO`, `SIZEALL`, `CUSTOM - not a standard system cursor` (the app's own custom cursor, e.g. the Codex app's SVG pointer), or `NO CURSOR - the OS has NO cursor for this window` (deliberately **not** called "SUPPRESSED": the Win32 `CURSOR_SUPPRESSED` flag means the pointer is driven by **touch or pen**, not that the cursor is hidden — the script prints that flag separately, correctly named, in the `flags=0x… (SHOWING=… SUPPRESSED=…)` line). |
| **0a** | **Pointer trails ON** | **Either route works** on Windows 11 24H2+ (this build is 26H2): **Settings → Accessibility → Mouse pointer and touch → Pointer trails → On**, or the Control Panel route **Settings → Bluetooth & devices → Mouse → Additional mouse settings → Pointer Options tab → Display pointer trails**. Both are legitimate: Microsoft's accessibility support article documents the *Pointer Options* route, and Microsoft's own Windows Server 2025 [KB5065426](https://support.microsoft.com/help/5065426) release note documents pointer trails under *Settings > Accessibility > Mouse and Mouse pointer and touch*. | Trails force Windows onto the **software** cursor path (a hardware cursor cannot leave a trail). **The arrow appears everywhere** ⇒ nothing is covering it ⇒ **mechanism B** (the hardware cursor plane is not refreshed / not scanned out over that region). **The arrow still vanishes over the affected region** ⇒ something really is drawn over it, or the app suppresses it ⇒ **mechanism A or C**. **If the arrow appears everywhere, you also now have a working workaround** — trails on is ugly but it is a pointer you can see, and it needs no reboot and no admin. |
| **0b** | **Does the *content* stutter, and is the pointer steady or flickering?** | Does the affected window's **content** freeze, tear, partially repaint or fail to update (often only near the pointer) as you move across it, **while the pointer itself stays steady**? Hover the same affected surface for ~30 s and watch the content, not just the arrow. | **The content freezes / tears / partially repaints while the pointer is steady** ⇒ that is the Composed ⇄ Independent-Flip flip-flop, and it is a *different* bug from this one — the pointer is the element that keeps updating through that transition, so it cannot explain a pointer that is *steadily* gone. **The pointer flickers** (appears and disappears as you move) ⇒ also that flip-flop family. **The pointer is steady-absent and the content animates perfectly** ⇒ a stable occlusion or an app-level suppression, i.e. this bug. |
| **0c** | **Move the window to the other display** | Drag the affected Explorer / VS Code window to the HDMI display, and to the DisplayPort display, and hover the same region. | The symptom **follows the display** ⇒ a display/driver path (A or B). The symptom **follows the app** ⇒ an app-level path (**C**). This is not cosmetic here: MPO capability is **per output** and mode-dependent on this machine — as currently reported, the primary has *no* overlay plane and the secondary has three. |
| **0d** | **Hardware acceleration off, in the affected app** | VS Code: `code --disable-gpu` (or `"disable-hardware-acceleration": true`). Chromium/chat app: `--disable-gpu`. Then hover the same region. **Use a separate instance** so your real profile is untouched: `code --disable-gpu --user-data-dir %TEMP%\code-gpuoff`. Narrow it afterwards with `--disable-gpu-compositing` and `--disable-direct-composition` (the last is a real Chromium switch — `ui/gl/gl_switches.cc`). | The pointer **returns** ⇒ the GPU/composition path was responsible ⇒ **mechanism B**. **No change** ⇒ the composition path is exonerated; A or C becomes more likely for that surface. See fix step 3 below. **Not applicable to the Explorer nav pane** — it is classic GDI (`SysTreeView32`), and `explorer.exe` has no such switch. |
| **0e** | **Force a repaint of the affected region** | Hover the affected region, then **scroll** it (or click once in an empty part of it). Does the pointer come back for a moment? | The pointer **returns on the repaint** ⇒ the region was simply not being re-scanned-out with the cursor plane ⇒ **mechanism B**, and a fairly specific one (a repaint-driven cursor-plane refresh failure). This is the cheapest test of the three, and it is worth doing **first** because it takes five seconds. |
| **0f** | **Read the CSS cursor in the Chromium surfaces** | In VS Code: `Help → Toggle Developer Tools` (F12). In a Chromium app: F12. Then select the element under the pointer and read **Computed → `cursor`**. | The computed value is **`none`** ⇒ the **CSS** variant of mechanism C — the app's own stylesheet is hiding the pointer. That is an **app-side** fault: no registry value, driver or MPO change will help, and the fix belongs to the app. Anything else (`auto`, `default`, a `url(...)` custom cursor) ⇒ the CSS is not the cause. |
| **0g** | **Reset the graphics stack** | `Win` + `Ctrl` + `Shift` + `B`. The screen flashes and DWM re-creates its surfaces. | The pointer **comes back for a while** ⇒ the fault is a **composition-state** problem, not a permanent capability limit. The effect is transient — this is a **diagnostic**, not a fix. If nothing changes, the state is not the problem. |

> **If the probe reported `MECHANISM C` for a surface, skip 0a, 0d and
> 0e for that surface.** 0a and 0d cannot tell A from C — their only
> possible answers are "A or C" — so on a surface the probe has already
> called C they can add nothing, and their wording ("A or C becomes
> more likely") points you at the overlay-plane branch of the fix
> ladder, which cannot help a C surface. Do 0a, 0b, 0c, 0d and 0e only
> on the surfaces the probe reports as **`not C`**, where they separate
> A from B. 0b (flicker vs steady) and 0c (move the window to the
> other display) are worth doing on any surface — 0c alone separates an
> app-level cause from a display-level one.
>
> **One hover is one sample, and one sample is not a verdict.** The
> document's own C confirmation rested on 2956 samples over 90 seconds
> at one position. The census prints a **sample count per row** — read
> it. A row that says `1 samples` has told you nothing; a row that says
> `not C` on a surface you are sure is broken has only excluded C *at
> that moment*. Dwell on each surface and sweep the pointer over it so
> the row count is high. The watch loop records a sample **only when
> the pointer moves**, so parking the pointer on one surface yields one
> sample for that surface. And the census is keyed on (process, window
> chain, display, **cursor state**): if one surface produces **two rows
> with opposite verdicts**, the suppression there is **intermittent** —
> which is itself the answer, and not a contradiction.
>
> **The census is keyed on the process as well as the class chain.**
> Every Chromium/Electron app on this machine reports the same class
> chain — `Chrome_WidgetWin_1 > Chrome_RenderWidgetHostHWND` (Brave,
> VS Code's tree and the ChatGPT app alike) — so the **process id is
> part of the key**, and each row header names the program. If you need
> to tell two Chromium surfaces apart, **run the probe one app at a
> time** (bring the app of interest to the front and the others out of
> the way).

> **Note on 0a.** Pointer trails **cannot be turned on from the API on
> this build.** `SPI_SETMOUSETRAILS` (0x005B) was tested three ways —
> by pointer, by value, and with `SPIF_UPDATEINIFILE | SPIF_SENDCHANGE` —
> and every call returned `TRUE` while changing nothing
> (`HKCU\Control Panel\Mouse\MouseTrails` stayed `"0"`, and
> `SPI_GETMOUSETRAILS` (0x005E) kept returning 0). So it has to be the
> **Settings toggle** above. There is no `SM_MOUSETRAILS` system metric;
> the value is read with `SPI_GETMOUSETRAILS` only.
>
> **One trap.** Turning trails on is itself known to *break* the cursor
> in Chromium apps: [microsoft/vscode#239110](https://github.com/microsoft/vscode/issues/239110)
> is literally titled *"Activating display pointer trails makes text
> editing pointer disappear"*, and the VS Code maintainer closed it as
> **not planned** on 2026-08-27 with *"This is a known Windows 11 24H2
> bug that affects cursor rendering in Chromium-based applications
> (including VS Code's Electron shell) […] the root cause is in
> Windows / Chromium rather than VS Code."*
>
> **A cleaner alternative, if trails-on is too risky on this machine:**
> the community `HKCU\Control Panel\Mouse\MouseTrails = -1` trick,
> which *suppresses* the trail while leaving hardware-cursor support
> disabled — i.e. Windows keeps drawing the cursor itself, in software,
> so you get a visible pointer with no trail and no Chromium trap. It
> needs no admin and no reboot. Treat it as an **optional
> community-sourced** route, not a primary instruction: it is a single
> end-user source, `-1` is not a documented value of that setting (the
> documented ones are 0 = off and 2–7 = trail length), and the API
> route for trails already failed on this build, so **verify the value
> actually took** before drawing any conclusion from it.
>
> So if turning trails on makes the pointer vanish in **VS Code or a
> Chromium app** where it was previously fine, that is **the other
> known bug**, not this one — and it does not invalidate the test. Read
> the result on a surface that was **already** affected, and on a
> surface that was **not**:
>
> - Trails on → the pointer now shows over the **already-affected**
>   region ⇒ **mechanism B** (and trails are now a workaround).
> - Trails on → the pointer now vanishes over a **previously fine**
>   region ⇒ the trails bug, not yours. Turn trails off and ignore.
> - Trails on → the pointer **still** vanishes over the already-affected
>   region ⇒ **mechanism A or C**.

**Verifying the presentation path (for the technically inclined).**
If you want to know whether a given app is actually being handed an
overlay plane, use **PresentMon**
(<https://github.com/game-dev/PresentMon>). Run it while the affected
window is in front and read the `PresentMode` column:

| PresentMon says | Meaning |
|---|---|
| `Hardware Composed: Independent Flip` | The app *"is using 'flip model' swapchains, and has been granted a hardware overlay plane"* — **this is the MPO signal**. |
| `Composed: Flip` | The app *"is windowed, is using 'flip model' swapchains, and is sharing its surfaces with DWM to be composed"* — no overlay plane. |
| `Hardware: Independent Flip` | iFlip: the app *"does not have ownership of the screen, but is still swapping the displayed surface every frame"* — DWM bypassed, **not** an overlay-plane grant. |

This is the direct way to confirm the MPO assignment on a given surface,
and it is the check the `dxdiag` numbers cannot give you (`dxdiag`
reports what the **output** supports, not what a **window** is doing) —
but it is a summary label, not a per-plane report, so the
dxdiag/dxcap/D3DKMT caps remain independent evidence.

### 0b. If the probe says MECHANISM C — what to do

**For that surface, steps 1–5 do not apply.** Mechanism C means the OS
has *no cursor at all* for the window under the pointer, so there is
nothing on the display side to reveal: no driver, registry, MPO or
compositing change can supply a cursor the application has asked
Windows not to draw. Steps 1–5 are for the surfaces the probe reports
as **`not C`**.

Which of the two C-variants you have decides what you can do, and only
the second has a user-facing lever:

| Variant | What it looks like | What you can do |
|---|---|---|
| `SetCursor(NULL)` from a `WM_SETCURSOR` handler | the app hides the pointer over the region it owns | App-side only. Look for a mode to leave — for the Codex/ChatGPT app the injector is gated on **comment interaction mode** (§"The Codex 'Owl' `cursor: none` injector"), so leaving that mode and re-testing is the test. Otherwise report it to the app's vendor. |
| CSS `cursor: none` in a Chromium/Electron app | the pointer is gone over the whole app, or over a region the app styles | **This one you can override.** Open the app's developer tools (a browser: F12; VS Code: Help → Toggle Developer Tools), put the pointer over the affected region, select the element in the Elements panel and switch off the `cursor: none` declaration in the Styles panel — the OS pointer returns immediately, which also confirms the diagnosis. For a durable fix in a browser, a user-stylesheet / custom-CSS extension that injects `* { cursor: auto !important; }` carries the same override; whether it wins against the app's own `!important` depends on the injection origin, so try the DevTools route first to confirm the cause. |

**The concrete test for the chat surface.** The Codex/ChatGPT app's
injector is real (§"The Codex 'Owl' `cursor: none` injector") and most
of its call sites are gated on **comment interaction mode** (see the
table there for the exact gates). So for the chat area: put the pointer
on a sent message, then leave comment interaction mode by whatever the
app provides for it (Escape, or clicking away from the message) and
re-test. If the OS arrow comes back, the injector was engaged — and
note that the app then draws **its own** pointer in its place, as a
**real custom OS cursor** (`._CommentMode_1gipt_9`, an SVG `url(…)` with
a `crosshair` fallback), not as the `.annotation-selection-cursor-bubble`
/ `-beam` elements — those are transition rules for a bubble and a beam
that accompany the annotation, not the pointer. So look for the app's
crosshair rather than the Windows arrow.

### 1. Take the Windows build that contains the fix — reversible, but not by a switch

Enrol in the **Windows Insider Program** → Experimental channel, and
install 26H2 build 26340.x. This is the build Microsoft shipped the
cursor fix in.

**Applies to:** the surfaces the probe reports as `not C`. It cannot
fix a mechanism-C surface — there is no cursor there to display.

**Read this before you enrol: leaving the Experimental channel is not
a toggle.** Microsoft splits the cases:
"Beta, Experimental, or Release Preview channel: You can flip the switch
to stop getting preview builds on your device. This wil put your device
back into a retail build." / "**Experimental (Future Platforms)
channel: You should use these instructions to complete a clean
installation of Windows 11**" —
[Stopping Insider Preview builds](https://learn.microsoft.com/en-us/windows-insider/troubleshooting).
The channel this step asks for is **Experimental**, so the documented
way off it is a **clean installation of Windows 11**, and Microsoft's
own note for a clean install is that it "will erase all of your files,
settings, and applications"
([Updating from an expiring Insider Preview build](https://learn.microsoft.com/en-us/windows-insider/build-expiration)).
Back up first, and read this as a reinstall, not a toggle.

If you install the build and want out within ten days, there is a
rollback: Settings → System → Recovery → *Go back to the previous
version of Windows 11*. Microsoft: "You can do this for up to 10 days
(or until you take the next build) after installing the update without
having to completely reset your device."

(If you would rather not join the Insider programme, the fix should
reach the 26300 GA branch in a later monthly update — but that is
waiting, not fixing.)

### 2. Disable MPO — the broad fix, reboot required (secondary output only, as reported)

This is NVIDIA's own published remedy (`mpo_disable.reg`, from KB 5157
— published in the 461.09 driver era, for flicker and cursor-plane
problems), and the remedy that users report Microsoft support
recommending for this symptom class. It comes in two strengths.

> **Read this before you apply it.** Disabling MPO can only change
> behaviour on an output that **has** overlay planes. As currently
> reported, that is the **secondary HDMI output only**; the primary
> DisplayPort output reports `MPO Caps: Not Supported` and has nothing to
> disable. (Those readings are per output and mode-dependent, so
> re-check them if the display configuration changes.) So this step is
> worth trying **if the affected surfaces are on the secondary display**
> (the chat app is), and is **unlikely to help the Explorer / VS Code
> symptoms on the primary** — those need a different explanation
> (mechanism B) and a different fix.
>
> It is also **community-reported** that on 25H2 the DWM-path value
> `OverlayTestMode` may be ignored in favour of the
> `GraphicsDrivers\DisableOverlays` value, which is why both are
> offered below. **That claim is disputed, and the dispute is on the
> record.** The maintainer of the MPO-GPU-FIX tool, whose own issue
> thread first carried the claim, later wrote:
>
> > *"All in all, DisableOverlays works. And so does the
> > OverlayTestMode."* — *"The results vary per build, as it always
> > has."*
> >
> > *"Seems some users still use this misinformation about the mpo fix
> > not working on win11 25h2+ (which is false)."* — *"If people say
> > that OverlayTestMode doesn't work on whatever version of win11,
> > it's a false statement."*
>
> WinUtil reached the opposite conclusion and **shipped** a change:
> PR #4495 (*"Add Game Mode toggle and fix MPO overlay for 25H2"*)
> replaced `\WPFToggleMultiplaneOverlay`'s `\Dwm\OverlayTestMode\` —
> *"which is deprecated on 25H2"* — with
> `\HKLM\...\GraphicsDrivers\DisableOverlays = 1`.
>
> The distinction is worth keeping either way: the **DWM-path** keys
> only *force MPO onto a single plane*, whereas `DisableOverlays`
> genuinely **disables overlay scheduling**. One independent report
> records `OverlayTestMode = 5` **+** `OverlayMinFPS = 0` + a reboot as
> having **no effect**, so treat the whole ladder as a test, not a
> certainty.

**Applies to:** mechanism A/B, **secondary output only**. Skip it for
a surface the probe reports as `MECHANISM C`.

**Step 1 — compatibility method (start here).**

```powershell
# Elevated PowerShell
reg add "HKLM\SOFTWARE\Microsoft\Windows\Dwm" /v OverlayTestMode /t REG_DWORD /d 5 /f
# then reboot
```

That is the exact content of NVIDIA's `mpo_disable.reg`. It is also
what WinUtil's "Disabled (Compatibility)" preset writes. (NVIDIA also
publishes an `mpo_restore.reg` to undo it; this document does not
quote that file's contents, because it does not reproduce them — the
undo path given below *deletes* the value, and
`scripts\fix-mouse-cursor.ps1 -Action Revert` does the same, so the
value is absent again afterwards and `-Action Status` reports
`OverlayTestMode: not set (Windows default)`. If you use NVIDIA's
`mpo_restore.reg` instead, check with `-Action Status` that the value
reads "not set" and not "0", and delete it yourself if it does not.)

> ```
> Windows Registry Editor Version 5.00
> [HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm]
> "OverlayTestMode"=dword:00000005
> ```

**Shut down and power back on — do not just restart.** NVIDIA's KB 5157
says only *"Restart your PC to apply the changes"* (the live wording;
the 2021 original said *"reboot your PC to complete the changes"*), but
the NVIDIA GeForce Forums thread that
reports the *cursor* specifically specifies *Win+X → Shut down → Shut
down* (a full power cycle, second "Shut down" to bypass Fast Startup)
and then powering back on, because a restart keeps the kernel session
alive and the override can appear not to have worked. Use the power
cycle. The same applies to the revert.

**Step 2 — full disable (only if step 1 is not enough).**

```powershell
# Elevated PowerShell
reg add "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v DisableOverlays /t REG_DWORD /d 1 /f
# then reboot
```

This is half of WinUtil's "Fully Disabled" preset. **That preset writes
both** — `Dwm\OverlayTestMode = 5` **and**
`GraphicsDrivers\DisableOverlays = 1` — so steps 1 and 2 together
reproduce it exactly, and `-Action Apply -Level Full` does both in one
go. It is the aggressive option: `DisableOverlays` is known to break
DX12 games, and there are community reports of it breaking **VRR /
Adaptive Sync** as well, so treat it as step 2, not step 1. The
concrete report is WinUtil's own issue tracker — issue **#4896**
documents `DisableOverlays = 1` causing flickering and crashes in
fullscreen / borderless games on 25H2 (an RX 9070 XT) — which is a
forum issue rather than a vendor statement, but it is the same value
and the same class of machine.

**Verify that the override took effect.** After the reboot, run `dxdiag`
→ *Save All Information* → search for `MPO`.

> **Do not use the primary display to check this.** The primary output
> **already** reads `MPO MaxPlanes: 1` with MPO nominally enabled,
> because that display has no overlay planes at all. Checking the
> primary is a **false positive**: it reads 1 either way.

The only output whose values can change is the **secondary (HDMI)** one:

| Output | Before | After (MPO off) |
|---|---|---|
| `\\.\DISPLAY1` primary (DisplayPort) | `MaxPlanes: 1` / `Caps: Not Supported` | `1` / `Not Supported` — **unchanged, tells you nothing** |
| `\\.\DISPLAY2` secondary (HDMI) | `MaxPlanes: 4` / `Caps: RGB,YUV,…,HDR (MPO3)` | *may* fall to `1` / `Not Supported` — but **not** reliably |

Read that number as a **capability report, not a usage counter**:
"MPO MaxPlanes" is the driver-advertised number of planes that "can be
supported in the current mode", whereas the step-1 switches
(`OverlayTestMode`, `OverlayMinFPS`) are DWM-side **assignment**
switches — `OverlayMinFPS` "disables its minimum frame rate requirement
for **assigning** DirectX swap chains to overlay planes". So:

- `MaxPlanes` falling to `1` is a **bonus, not a requirement**. The
  MPO-GPU-FIX tool's issue expects `DisableOverlays = 1` to make dxdiag
  read *"Not Supported"* or *"MaxPlanes: 1"*, but the same maintainer
  also says dxdiag *"only reports the number of planes the gpu
  supports. Nothing more."* — and that their own tool *"will never
  change the number nor say Not Supported on DxDiag."* So the reported
  plane count may not move **even when the behaviour does**.
- `MaxPlanes` **not** falling is **not** evidence that the override
  failed, and `MaxPlanes` falling is **not** proof that it succeeded.
- The behaviour to check is whether DWM stops promoting swap chains to
  overlay planes — in PresentMon, the "Hardware Composed: Independent
  Flip" row should give way to "Composed: Flip" — together with whether
  the symptom itself moves. NVIDIA's own `MPO_INDICATOR` profile option
  is the other way to see it.

`scripts/diagnose_display_split.py` prints the display bounds and which
window is on which display; it does not read dxdiag, but the bounds it
prints tell you which of the two rows above applies to the surface you
are testing.

**Undo everything:**

```powershell
reg delete "HKLM\SOFTWARE\Microsoft\Windows\Dwm" /v OverlayTestMode /f
reg delete "HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers" /v DisableOverlays /f
# only if you also set OverlayMinFPS = 0 (the flicker knob, see below):
reg delete "HKLM\SOFTWARE\Microsoft\Windows\Dwm" /v OverlayMinFPS /f
# then shut down and power back on - do not just restart
```

`scripts\fix-mouse-cursor.ps1 -Action Revert` removes all three of
`OverlayTestMode`, `DisableOverlays` and `OverlayMinFPS`, so it undoes
more than this block does — including the `OverlayMinFPS = 0` this
document tells you to set for the flicker variant. Use a full power
cycle (Win+X → Shut down → Shut down), not a restart, for the same
reason as above: a restart keeps the kernel session alive and the
override can appear not to have been undone.

#### `OverlayMinFPS` set to zero — the right knob for one symptom, the wrong knob for another

> **An earlier version of this document said "do not set
> `OverlayMinFPS = 0`" unconditionally. That was too strong, and it is
> corrected below.** The knob is symptom-dependent, and the deciding
> word in Microsoft's own documentation is **"stay"**.

`OverlayMinFPS` is the *only* overlay registry value Microsoft
documents — on the [DWM Registry Values](https://learn.microsoft.com/en-us/windows/win32/dwm/registry-values)
page, under `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm`:

> *"A DWORD. If this value is present and set to zero, the Desktop
> Window Manager disables its minimum frame rate requirement for
> assigning DirectX swap chains to overlay planes in hardware that
> supports overlays. This makes it more likely that a low frame rate
> swap chain will get assigned and stay assigned to an overlay plane,
> if available. This mechanism was introduced in its present form in
> Windows 11. **This registry value may be modified or removed in future
> releases.**"*

Microsoft separately documents a per-adapter WDDM feature-override
mechanism,
`HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\<XXXX>\Features\<FeatureID>`
with `Enabled` / `MinVersion` / `MaxVersion` / `AllowExperimental`.

> **Two path caveats, because they are reported to differ.** Microsoft
> documents `OverlayMinFPS` under `HKLM\SOFTWARE\Microsoft\Windows\Dwm`
> (DWM Registry Values page); much community advice writes it under
> `HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers` instead —
> the same key that `DisableOverlays` lives under. **The sources
> disagree, and the two keys are not interchangeable.** Reverse-engineering
> notes give a **default of 15** (only swap chains rendering at ≥15 FPS
> are eligible for an overlay plane). Set it in the
> **Microsoft-documented** location, and check `-Action Status` to see
> which one the script can actually see — the script reads and removes
> the **Dwm** key only, so a value someone else put in
> `GraphicsDrivers` will survive `-Action Revert` and has to be deleted
> by hand.
>
> And on this build the whole knob is a **weak test**: the community
> write-up that uses the `GraphicsDrivers` path states plainly that
> *"the 24H2 registry path no longer takes effect"*, so on 25H2/26H2
> `OverlayMinFPS` may do nothing at all. `GraphicsDrivers\DisableOverlays
> = 1` (step 2) is the stronger lever on this build.

So `OverlayMinFPS = 0` does two things: it makes more surfaces
**eligible** for an overlay plane, and it makes an existing assignment
**stick**. That gives it opposite meanings for two different symptoms:

| Symptom | What `OverlayMinFPS = 0` does | Verdict |
|---|---|---|
| The **window content** freezes, tears or stutters — the surface keeps re-presenting, sometimes partially, sometimes not at all (the 24H2/25H2 Composed ⇄ Independent-Flip flip-flop in Chromium/Electron apps) | Makes the overlay assignment **stable**, so the surface stops flip-flopping between presentation paths mid-frame | **The right knob.** This is the bug the advice was written for. |
| The pointer is **steadily gone** over an affected region (this symptom) | Makes overlay assignment **more likely and more stable** — i.e. more of the screen is on an overlay plane for longer | **The wrong knob.** It moves the machine *towards* the suspect condition. |

> **A correction to an earlier version of this table**, which put
> *"the pointer flickers / drops out intermittently as you move"* in
> the first row. That conflated two different bugs. The **documented**
> symptom of the 24H2 presentation-path flip-flop is the **window
> content** freezing, tearing or partially freezing — the Chromium bug
> tracker and the write-ups describe *"rendering / partial freezing"*,
> and the microsoft/terminal discussion (#19050) describes DWM forcing
> `Composed: Flip` over `Hardware Composed: Independent Flip` in
> windowed mode. The **pointer is the element that keeps updating**
> through that transition, which is precisely why the flip-flop was
> never a plausible explanation for a pointer that is *steadily* absent.
> So the row above is not "the pointer flickers" — it is "the content
> stutters", and the two are different observations.
>
> And the flip-flop bug itself is **community-documented, not
> Microsoft-acknowledged**: the repro repository's own README says it
> is *"Not yet acknowledged on the official 24H2 known-issues page"*.
> Its repro instructions describe the symptom precisely — *"the counter
> freezes at an old number while the rest of the page UI is still
> responsive (cursor, buttons, scrolling)"* — i.e. **the pointer keeps
> working** while the content behind it freezes.

That is also why the community advice pairs `OverlayMinFPS = 0` with
`OverlayTestMode = 5`: the two are frequently conflated, but they are
aimed at different bugs. `OverlayTestMode` is not on that Microsoft
page at all — it is undocumented, and it is NVIDIA's and WinUtil's
remedy, not Microsoft's. (Decoding NVIDIA's own `mpo_disable.reg`
confirms it writes `OverlayTestMode = 5` and **no** `OverlayMinFPS`
value.) On 25H2 the DWM-path values are **community-reported** to be
ignored in favour of `GraphicsDrivers\DisableOverlays` — disputed, but
the distinction is real: the DWM-path keys only *force MPO onto a
single plane*, whereas `DisableOverlays` genuinely **disables overlay
scheduling**.

> **The 25H2 "no longer takes effect" story, in full, and how solid it
> is.** It is **community documentation only** — not on any Microsoft
> known-issues page — and it is **disputed**. The MPO-GPU-FIX
> maintainer calls it *"misinformation"* and reports `OverlayTestMode`
> still working on 25H2 and 26H1 across numerous builds. The
> per-build picture reported across sources is roughly:
>
> | Build | Reported working path | Reported status |
> |---|---|---|
> | 22H2 / 23H2 | `Dwm\OverlayTestMode` = 5 | works |
> | 24H2 | `GraphicsDrivers\DisableOverlays` = 1; `OverlayMinFPS` = 0 under `GraphicsDrivers` | works, per some reports |
> | 25H2 | `GraphicsDrivers\DisableOverlays` = 1 | a workaround, with caveats |
>
> Two specific reports are worth carrying: Windows 11 25H2 build
> **26200.7628** partially restored the presentation behaviour, but
> **only above ~20 FPS**, and `OverlayMinFPS` still had **no effect**
> there; and a Chromium bug-tracker note records `OverlayMinFPS` = 0
> under `GraphicsDrivers` still working in 24H2, while 25H2 requires
> `DisableOverlays`. None of this is Microsoft-acknowledged.

**Field reports exist in both directions**, which is itself the
answer — but read them in context. Citrix's KB CTX579816 and the
Microsoft Q&A thread 5805562 record cases where `OverlayTestMode = 5`
**alone** restored a vanished cursor: Citrix's is explicitly an **AMD
graphics driver** MPO issue, and the Q&A case is a **10ZiG 7111q thin
client on Windows 365** (Windows 11 24H2 via Windows App, cursor
disappearing over Excel/Word/Outlook document canvases) where the
requester replied *"Seems to have worked for now."* Neither is a
consumer NVIDIA desktop like this machine, and both are
MPO-promotion-specific failures. Other reports record
`OverlayTestMode = 5` **+** `OverlayMinFPS = 0` + a reboot having **no
effect**. There is no registry value that reliably fixes this; there is
a *diagnostic* that narrows the mechanism.

**What to do with it here.** Leave it unset unless the *content
stutter* variant is what you actually have. `scripts/fix-mouse-cursor.ps1`
**reports** `OverlayMinFPS` but never writes it, and `Revert` removes it
in case something else set it. If the affected app's **content**
freezes or stutters while the pointer stays put and steady (see by-eye
test **0b** above), set it to `0` under
`HKLM\SOFTWARE\Microsoft\Windows\Dwm` — the Microsoft-documented
location, and the one to use; the community path is a different key
(`HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers`) and the
sources disagree — and re-test. That is the one case where it is the
right answer, and even then it is a weak test on 25H2, where the DWM
path is community-reported not to take effect, so
`GraphicsDrivers\DisableOverlays = 1` (step 2) is the stronger lever.
For a pointer that is *steadily absent* while the content animates
perfectly, it is the wrong knob, and setting it would push the machine
towards the suspect condition.

#### The script

```powershell
# If the machine's execution policy blocks the script, widen it for THIS
# shell only - it is session-scoped and changes neither the machine nor the
# user policy - and then call the script:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned

.\scripts\fix-mouse-cursor.ps1                          # Status (read-only)
.\scripts\fix-mouse-cursor.ps1 -Action Apply            # step 1
.\scripts\fix-mouse-cursor.ps1 -Action Apply -Level Full # steps 1+2
.\scripts\fix-mouse-cursor.ps1 -Action Revert           # undo all three values
```

The one-liner form `powershell -ExecutionPolicy Bypass -File .\scripts\fix-mouse-cursor.ps1 …`
also works, but `-ExecutionPolicy Bypass` disables script-execution
enforcement for that invocation — use it deliberately, not reflexively.
The `Set-ExecutionPolicy -Scope Process` form above is the narrower
equivalent.

`Apply` and `Revert` need an Administrator shell and a **full power
cycle** to take effect. Some report a small performance cost from
disabling MPO (mainly in borderless-windowed games).

**What `Status` prints.** It is read-only, and besides the MPO state it
prints the **live cursor state at the pointer** —
`Cursor flags : 0x… (SHOWING=… SUPPRESSED=…)`. `SHOWING=0` is the
**mechanism C** signature described in §Verdict: the OS holds no cursor
for the window under the pointer, so the application suppressed it, and
the script prints the `MECHANISM C` verdict right there.
`SHOWING=1` means the OS had a cursor to draw there and the fault is in
the display path. (The script's own explanatory line under that output
— *"Windows is NOT hiding the pointer - it is being covered, or the
hardware cursor plane is not refreshed there"* — was written before
mechanism C was measured; on a `SHOWING=0` surface the app **is**
hiding the pointer.) Put the pointer on the surface you care about
before running it: the reading is of the pointer's position, not of the
application.

### 3. Disable GPU compositing in the affected app — no reboot

**This is the cheapest test that discriminates between the two
candidate mechanisms, and it should be done before anything that needs a
reboot.** It targets mechanism B (the GPU/composition path failing to
refresh the hardware cursor plane) directly.

**Applies to:** mechanism B. Skip it for a surface the probe reports as
`MECHANISM C`.

VS Code:

- `code --disable_direct_composition=1`
- or `"disable-hardware-acceleration": true` in `settings.json`
- or `code --disable-gpu`

Chromium / the chat app:

- `--disable-gpu`
- `--disable-gpu-compositing`
- or the equivalent per-profile setting

**How to read the result.**

| Outcome | Conclusion |
|---|---|
| The pointer **comes back** over the affected surface | The GPU/composition path was responsible → **mechanism B**. |
| **No change** | The composition path is not the cause → the overlay-plane path (**mechanism A**) or an app-level cursor suppression becomes more likely for that surface. |

This is per-app, reversible (delete the flag), and needs no reboot. It
does not *fix* anything permanently — it is a **diagnostic** first, and
a workaround second.

### 4. Reset the graphics stack — no reboot

`Win` + `Ctrl` + `Shift` + `B`. The screen flashes and DWM re-creates
its surfaces; if the pointer comes back for a while, that is further
confirmation that it is a composition-state problem.

**Applies to:** mechanism A/B surfaces. A mechanism-C surface will not
change.

### 4b. Turn HAGS off — reboot required

`HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers\HwSchMode = 1`
disables **Hardware-Accelerated GPU Scheduling**. It is **on** on this
machine today (`dxdiag` → *Hardware Scheduling: Enabled: True* on both
targets), and HAGS changes how the scheduler hands buffers to the GPU —
which is exactly the layer a cursor-plane refresh failure would live in.
It is a **class elimination**, not a targeted fix: it will not help a
mechanism-C surface, and it is only worth doing if the by-eye tests say
A/B *and* steps 2–3 came back negative.

Undo: delete the `HwSchMode` value and reboot.

**Applies to:** mechanism A/B surfaces. A mechanism-C surface will not
change.

### 5. GPU driver

The installed driver is **576.02** (build 32.0.15.7602, `DriverDate`
`12/04/2025`); NVIDIA's own lookup lists **621.01 (21 Oct 2026)** as the
current WHQL Game Ready driver for this GPU. Update if you want to try
a different branch.

**But be honest about the odds.** Parsing the release notes from 565.90
through 621.01 turns up **no** mention of cursor, hardware cursor,
DirectComposition or HAGS in any of them. There is no changelog entry
pointing at this symptom, so this step is a **shot in the dark**, not
an evidence-based fix. Two things make it worth doing anyway: it is the
cheapest way to change the driver's plane behaviour, and it **will**
change MPO behaviour on the HDMI output — so a driver update doubles as
an MPO test, and if the symptom moves on the secondary, that is a
signal in itself.

**Applies to:** mechanism A/B surfaces. A mechanism-C surface will not
change.

### Not fixes (despite being listed upstream)

Pointer size, pointer theme and pointer shadows cannot help: the arrow
is a two-tone glyph, so it was never hidden by contrast, and it is
being *covered or not refreshed* rather than camouflaged (§2b) — or,
in mechanism C, it is **not drawn at all**, and no change to how it
would be drawn can make it appear. They are still worth 30 seconds as a
**test**: if enlarging the pointer makes it appear over a region, then
the mechanism there is contrast/clipping rather than plane occlusion —
which is itself a useful answer, but not one this document's
measurements support anywhere.

Note that "the nav pane has a different cause from the Chromium apps"
is no longer a safe assumption in either direction: the nav pane is
**not** C (the OS had a real, showing cursor there — §Verdict), and the
Chromium apps are **C in one region and not C in another**. Different
surfaces can have different mechanisms; that is what the probe is for.

## Note on `cua-driver`

The trycua `cua-driver` runs an **agent cursor overlay** — a
full-desktop, per-pixel-alpha, `WS_EX_LAYERED | WS_EX_TRANSPARENT`
window (`Cua.AgentCursorOverlay`) that draws where the agent is acting.
It is **on by default**; the driver's own documentation says *"the OS
cursor is untouched"*, and it sits below foreground apps in z-order, so
it is **not** the cause of the above symptom.

It is still worth knowing about:

- The `cua-driver-serve` scheduled task starts the daemon **without**
  `--no-overlay`, so the overlay is present at every logon.
- The overlay is a full-desktop layered surface. **A `WS_EX_LAYERED`
  window is not an MPO candidate** — MPO applies to DirectX swap
  chains, and a GDI layered window is composited by DWM into the
  desktop plane, below the hardware cursor. So it is not the *cause*.
- **But it is a confounder, and that matters for every experiment in
  the fixes section above.** A full-desktop layered surface changes DWM's
  composition path for the whole desktop, which is exactly the thing that
  decides whether a surface is presented as `Composed: Flip` or
  `Hardware Composed: Independent Flip` — i.e. whether an overlay plane
  is involved at all. So the machine you are testing on is **not** a clean
  machine, and an MPO experiment run with the overlay present may not
  carry over to a machine without it.
- To remove it, start the daemon with `--no-overlay`, or use the
  driver's `set_agent_cursor_enabled` tool.

## Also worth cleaning up

An **orphaned `cua-driver.exe` (PID 3176)** from before the `close()`
fix in this repo was still running, started 2026-10-15 by the
`cua-driver-serve` scheduled task, holding a visible full-desktop
overlay window. It runs **elevated**, so a non-elevated shell cannot open
its handle or token (`OpenProcess` → access denied) and cannot stop it.
It exits at the next reboot. If you want it gone sooner, stop it by
**name** rather than by the id — the id above is from 2026-10-15 and is
reusable once that process has exited, and the scheduled task will start
a fresh driver with a new id:

```powershell
# elevated PowerShell
Get-Process cua-driver -ErrorAction SilentlyContinue |
    Where-Object { $_.StartTime -lt (Get-Date).AddDays(-1) } |
    Stop-Process -Force
```

Note that this stops the agent's control of the machine until the
`cua-driver-serve` scheduled task starts the daemon again. If you only
want the overlay gone, prefer the non-destructive route above
(`--no-overlay` / `set_agent_cursor_enabled`) — that keeps the driver
running.

---

## How these facts were established

Scripts live in `scripts/` in this repository:

Run them **from the repository root**, with the interpreter this
repository uses — `.venv\Scripts\python.exe` if the virtualenv exists,
otherwise any Python 3.11+ on PATH:

```powershell
.venv\Scripts\python.exe scripts\cursor_shapes.py --watch 120
python scripts\cursor_shapes.py --watch 120          # equivalent
```

`cursor_shapes.py --watch` **requires a duration in seconds**
(`--watch 120` = two minutes); running it with no value prints an
error and exits with code 2. The forms that work from the repository
root are:

```powershell
python scripts\cursor_shapes.py                      # resolve the cursor now
python scripts\cursor_shapes.py --watch 120          # census, 2 minutes
python scripts\show_cursor_handles.py 0x1001F        # render a handle as text
python scripts\cursor_handle_map.py 0x1001F          # map handles to names
.\scripts\fix-mouse-cursor.ps1                      # Status (read-only)
.\scripts\fix-mouse-cursor.ps1 -Action Apply
.\scripts\fix-mouse-cursor.ps1 -Action Apply -Level Full
.\scripts\fix-mouse-cursor.ps1 -Action Revert
```

> If PowerShell's execution policy blocks the script, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`
> first. That is **session-scoped** — it changes neither the machine nor
> the user policy, and it ends with the shell. (The one-liner
> `powershell -ExecutionPolicy Bypass -File …` also works, but it
> disables script-execution enforcement for that invocation, so the
> narrower form is the better default.)

| Script | What it measures |
|---|---|
| `diagnose_cursor3.py` | `SM_CURSORS`, `SPI_GETMOUSEVANISH`, `GetCursorInfo`, window chain under the pointer |
| `diagnose_cursor6.py` | live cursor identity vs `LoadCursorW(NULL, IDC_*)`, AND/XOR mask populations |
| `diagnose_cursor7.py` | framebuffer sample around the pointer (is the arrow in the image?) |
| `diagnose_cursor10.py` | surface class of each region of a window, via `WindowFromPoint` |
| `diagnose_cursor12.py` | transparent / layered / overlay top-level windows and their owners |
| `diagnose_cursor13.py` | full framebuffer grab + frame diff (candidates for a drawn cursor sprite) |
| `diagnose_cursor14.py` | static geometric hit-test: which child window paints each region, its class cursor and its ex-style |
| `diagnose_cursor15.py` | probe of the graphics stack for a cursor-plane / overlay API |
| `diagnose_cursor16.py` | cursor-glyph template match: hardware cursor plane or software cursor |
| `diagnose_display_split.py` | display bounds + which display each window and the pointer are on — decides whether the overlay-plane mechanism can even apply to a given surface |
| `diagnose_owl_cursor.py` | byte scan of the Codex/Owl `app.asar` for CSS cursor suppression |
| `cursorframe.py` | the glyph reconstruction and matching used by the two scripts above |
| `cursor_is_composited.py` | the offset sweep (is the arrow in the image?) + the `SetCursor` effect check that shows the old magenta calibration was void. A sound **positive** test, not a sufficient negative one — see §2 |
| `cursor_software_probe.py` | the superseded first attempt, kept with the reason it fails |
| `watch_cursor.py` | 20 Hz correlation of pointer position vs window vs live cursor identity |
| `watch_cursor2.py` | 25 Hz correlation of pointer position vs deepest child window vs its `WNDCLASS.hCursor` |
| `watch_cursor4.py` | per-surface: window chain, live cursor, and hardware-vs-software determination |
| `watch_cursor5.py` | per-chain: class cursor of *every* ancestor + live cursor + glyph match + **which display**, to settle the nav pane |
| `cursor_shapes.py` | **the instrument that decides mechanism C** — reads `GetCursorInfo`, resolves the live cursor to a **shape** (ARROW / IBEAM / WAIT / CROSS / UPARROW / SIZEALL / HAND / NO / … / CUSTOM / NO CURSOR), and in `--watch` mode prints a per-surface census with a `MECHANISM C` / `not C` verdict. The `IDC_*` table in it is the **correct** one (the second group starts at 32640, not 32517). Its "no cursor" state is labelled **`NO CURSOR`**, never "SUPPRESSED", so it cannot be confused with the `CURSOR_SUPPRESSED` flag |
| `cursor_handle_map.py` | maps shared cursor **handle values** to system cursor names by loading the standard IDC cursors and printing their handles: `0x10003` = ARROW, `0x10005` = IBEAM, `0x1001F` = HAND, `0x10027` = PERSON. `LoadCursor` succeeds for every current `IDC_*` (the only exceptions are `IDC_SIZE` and `IDC_ICON`, which the header marks obsolete) |
| `mpo_per_source.py` | the **per-output** MPO capability query — `D3DKMTGetMultiPlaneOverlayCaps` for each VidPN source, which is the API-level statement of "MPO capability is per *output*" (§"Why mechanism A does not fit"). Read-only capability query |
| `show_cursor_handles.py` | renders a cursor handle as a text picture, so an unmapped handle can be identified by eye |
| `fix-mouse-cursor.ps1` | `-Action Status\|Apply\|Revert`; `-Level Compatibility\|Full` walks the `OverlayTestMode` → `DisableOverlays` ladder |

Almost all the **probes** are read-only. None captures personal file
contents, none reads a window title, a URL, a path, or any chat or
document content, and none modifies the registry, settings, files or
processes — with the two exceptions named below.

> **Disclosure — one probe did synthesise a pointer move.** The
> read-only discipline for this investigation was "no input is
> synthesised, no window activated or moved". **Two scripts break it:**
> `cursor_is_composited.py` and its superseded predecessor
> `cursor_software_probe.py` call `SetCursorPos` in a `jiggle(x, y)`
> helper — they shift the pointer **one pixel** to the right and
> immediately back (50 ms), as a controlled test of whether the arrow
> is composited into the framebuffer. That is a real pointer move, it
> happened on the user's desktop, and it is disclosed here rather than
> buried.
>
> One thing that move is **not** is *placement*. `jiggle(x, y)` is
> called with the position the pointer is **already** at, and it returns
> the pointer to that exact position; it cannot put the pointer over a
> tree or a list. The nav-pane reading in §Verdict was therefore a
> **pure read** by `cursor_shapes.py` — the pointer was over the
> nav-pane tree because that is where it was, and nothing in that probe
> moved it. Beyond that one-pixel nudge the pointer is not moved at all,
> no click is ever synthesised, no window is activated, raised or
> closed, and no keystroke is sent. Every other probe in the list —
> including `cursor_shapes.py`, the instrument that decides mechanism C —
> only *reads*.

The other exception is `fix-mouse-cursor.ps1`: its default `Status`
action is read-only, but `-Action Apply` **writes two registry values**
(`OverlayTestMode`, plus `DisableOverlays` at `-Level Full`) and
`-Action Revert` **deletes three** (`OverlayTestMode`, `DisableOverlays`,
`OverlayMinFPS`) — both need an Administrator shell and a power cycle.
The three newest scripts are also **content-blind by construction**:
they report window **classes**, process **ids** and cursor **shapes**
only — never a window title, a URL, a path, or any chat or document
content.

> **The one measurement that changed the diagnosis.** A targeted
> `GetCursorInfo` read over a Chromium render widget (Brave, pid
> 18400) at (901, 660) — on the **primary** display — returned
>
> ```
> hCursor = 0x0            (NULL)
> flags   = 0x00000000     (CURSOR_SHOWING clear, CURSOR_SUPPRESSED clear)
> ```
>
> stably, across **2956 samples over 90 seconds**, while 284 samples
> over other positions in the same app showed a live arrow. The OS had
> **no cursor at all** for that window — the application had suppressed
> it. That is mechanism C, confirmed, and it is what turned "only
> by-eye tests can decide this" into "one API call decides C, and only
> A-versus-B needs your eyes".
>
> **The two-hour census that confirmed it.** A second run of
> `cursor_shapes.py --watch 7200` collected **3919 samples over 22
> distinct (process, chain, display, state) combinations**. Inside one
> Brave render widget (pid 18400, primary display) it found **48
> mechanism-C samples at six distinct positions** — (901, 660),
> (906, 661), (1284, 587), (632, 702), (634, 698), (606, 686) —
> against **1259 samples of a custom cursor** and **1161 of a live
> ARROW** at other positions of the *same* widget. (The "custom cursor"
> label is **not reliable** — it was produced with a broken `IDC_*`
> table in which a link hand could not be recognised, so that bucket
> may be a mixture of `HAND` and a genuine custom cursor. The
> `MECHANISM C` rows are unaffected: they are read from the *flags*.)
> Every other app
> (Windows Terminal 248+16, the taskbar 56+20, Hermes 94+14+1,
> genspark 690+232, the Explorer window 5+4+4+3+3, and 4 CursorAudit
> samples ×3) reported a real cursor handle, i.e. **`not C`**. The
> same app, the same window, different region: the suppression is
> **per-region**, and it is the app's doing. The census **never visited
> the Explorer nav pane, the Windows Search list, the VS Code tree or a
> sent message**. The nav pane was measured separately, by a targeted
> probe (immediately below); the Windows Search list, the VS Code tree
> and a sent message remain **unmeasured**.
>
> **And the one that retired a hypothesis.** A probe over the
> **Explorer navigation pane** at (581, 302) returned
> `hCursor = 0x1001F` (a real handle — the pointing hand) with
> `flags = 0x00000001` (`CURSOR_SHOWING` set); over the neighbouring
> file list at (982, 478) it returned `0x10003` (`IDC_ARROW`) with the
> same flags. The OS had a cursor and reported it as showing, so
> mechanism **C is ruled out for the nav pane** — and the NULL
> window-class cursor that this document used to offer as the
> explanation is not a suppression mechanism at all (§3b).

> **One deliberate exception, and it is documented in the script
> header.** `cursor_is_composited.py` and `cursor_software_probe.py`
> move the pointer by **one pixel and back**, so the OS redraws it, and
> call `SetCursor()` once to test whether that call takes effect at
> all. No clicks, no keys, no scrolling, no window is moved or
> activated, and the pointer ends at its exact original coordinates.
