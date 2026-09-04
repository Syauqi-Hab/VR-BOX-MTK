# LensCast VR Studio

LensCast is a runnable MVP for watching a normal Windows desktop or PC game in a
phone VR Box. It mirrors the desktop to a phone over a local USB tunnel, duplicates
the image into left/right eye views, and exposes manual calibration for different
VR Box and full-screen phone layouts.

The server keeps MJPEG for the Studio preview and uses a demand-driven latest
JPEG frame route for the phone, so the WebView drops stale frames instead of
building an MJPEG decode queue. It binds to `127.0.0.1` by default; the phone
interface is a web app at /phone delivered through the USB ADB tunnel.

## What is included

- Live Windows desktop capture with a dependency-light Pillow/GDI fallback.
- Automatic DXGI Desktop Duplication capture for the 30 fps game profile, with
  Pillow/GDI fallback when DXGI is unavailable.
- OpenCV SIMD scaling on raw DXGI frames, with a four-worker cap so resize does
  not monopolize CPU cores needed by the game.
- A desktop control interface at /studio with an OBS-like live preview.
- Select the virtual desktop or a specific Windows monitor as the capture source.
- One-click quality profiles plus live bitrate, frame age, monitor, and headset status.
- Fixed stream-frame presets (`960x540`, `1280x720`, `1600x900`, and `1920x1080`) with
  automatic black letterboxing instead of stretched output.
- Game-aspect stream presets for `4:3` (`1280x960` and `1440x1080`) and `5:4`
  (`1350x1080`), including a center-content crop that removes matching game pillarbox
  before resize and JPEG encoding.
- Selectable JPEG chroma: efficient 4:2:0 for low latency or sharp 4:4:4 for
  colored text and UI detail when the USB bandwidth has headroom.
- One shared settings model: changing a Studio slider updates the HP renderer.
- Independent control for source crop, eye width/height, eye gap, offsets, zoom,
  barrel distortion, curvature, brightness, and fit mode.
- Named lens profiles saved locally on the PC, so separate VR Box calibrations can
  be saved, loaded, updated, or deleted without changing capture settings.
- Phone rendering through WebGL for two eye regions and lens distortion.
- Low-latency phone delivery: one fresh JPEG is decoded at a time, while stale
  frames are deliberately skipped when the phone cannot keep up.
- Optional high-contrast PC cursor overlay for DXGI/GDI captures that omit the
  Windows pointer.
- Optional native-sharpness mode that renders the phone canvas up to DPR 3 for
  compatible 1080x2400 displays; it uses more phone GPU power than the default DPR 2 mode.
- A Canvas fallback for phones/browsers without WebGL.
- A compact landscape onboarding card keeps the "Masuk mode VR" button reachable
  on short phone screens.
- Persistent settings in lenscast-settings.json after the first run.

## Start

For the normal USB workflow, double-click
[Open LensCast PC.bat](<C:/Abie/Passion Project/VR/Open LensCast PC.bat>) and
press **Connect USB**. The PC dashboard starts LensCast locally, creates the
ADB tunnel, and opens the installed LensCast VR app on the phone automatically.
Use **Buka Studio Kalibrasi** in that dashboard to tune the headset preview.

The original browser-first start is still available by double-clicking
[Start LensCast.bat](<C:/Abie/Passion Project/VR/Start LensCast.bat>), or by running:

~~~powershell
cd "C:\Abie\Passion Project\VR"
py -3.12 -m pip install -r requirements.txt
py -3.12 app.py
~~~

The Studio page opens automatically. If it does not, use:

~~~text
http://127.0.0.1:8264/studio
~~~

The bottom of Studio shows a URL similar to this:

~~~text
http://192.168.1.20:8264/phone
~~~

Open that URL from the Android phone while the phone and PC are on the same Wi-Fi.
Allow Windows Firewall access on the first launch, then rotate the phone to
landscape and tap **Masuk mode VR**.

## USB-C connection

There are two wired routes:

1. Turn on Android USB tethering. LensCast will show the PC address for that
   network adapter in Studio; open its /phone address from the Android browser.
2. For a direct ADB tunnel, enable Developer options and USB debugging on Android,
   connect the data cable, then double-click
   [Connect Android USB.bat](<C:/Abie/Passion Project/VR/Connect Android USB.bat>).
   Open http://127.0.0.1:8264/phone on the phone afterward.

The ADB option uses the USB data cable as the network path and avoids depending
on Wi-Fi. It requires the usual Android USB debugging confirmation on the phone.

## LensCast PC

`LensCast PC` is the one-click Windows control dashboard for the USB route:

1. Connect the Android phone by USB-C and keep USB debugging enabled.
2. Open [Open LensCast PC.bat](<C:/Abie/Passion Project/VR/Open LensCast PC.bat>).
3. Press **Connect USB**. The dashboard starts only a `127.0.0.1` server,
   restores `adb reverse` automatically after a USB reset, and opens LensCast VR
   on every connected authorized phone.
4. Press **Buka Studio Kalibrasi** for the OBS-style preview and optical controls.

To make a portable Windows executable, run
[Build LensCast PC EXE.bat](<C:/Abie/Passion Project/VR/Build LensCast PC EXE.bat>). It
creates `dist/LensCast PC/LensCast PC.exe` after the one-time PyInstaller installation.

The Wi-Fi card is visible but intentionally locked. USB is the safe default;
activating live desktop sharing over the local Wi-Fi requires explicit approval.

## Android APK

LensCast now includes a small native Android shell around the existing phone
renderer. It keeps the WebGL split-screen, manual VR calibration, and PC-managed
settings from `/phone`, but opens it as an installable landscape app instead of a
browser tab. The app is not a separate video encoder: LensCast PC must still be
running to provide the stream.

1. Build it by double-clicking
   [Build LensCast VR APK.bat](<C:/Abie/Passion Project/VR/Build LensCast VR APK.bat>).
2. Connect the Android phone with a data-capable USB-C cable, enable **USB
   debugging**, and accept the RSA prompt on the phone.
3. Double-click
   [Install LensCast VR APK.bat](<C:/Abie/Passion Project/VR/Install LensCast VR APK.bat>).
   It installs the debug APK and creates `adb reverse tcp:8264 tcp:8264`.
4. Start LensCast PC, then open **LensCast VR** on the phone. Its default address
   is `http://127.0.0.1:8264/phone`, which works through that USB tunnel.

The APK output is
`android/app/build/outputs/apk/debug/app-debug.apk`. It supports Android 8.0
(API 26) or later. Tap the connection chip, or long-press the image, to change
the saved PC endpoint when needed.

## Practical setup for games

1. Start LensCast before starting the game.
2. For Persona 3 Reload, keep Mesin capture on Otomatis and start with
   **Responsif**: DXGI, 60 fps target, JPEG quality 52, and 4:2:0 chroma. If the
   capture readout cannot hold near its target, use **Seimbang** before raising
   resolution. The capture note should say `DXGI Desktop Duplication aktif` and
   `OpenCV SIMD`.
   If it says Pillow instead, start LensCast through `Start LensCast.bat` so the
   Python 3.12 environment with the optimized dependencies is used.
3. Choose the monitor containing the game from Monitor sumber when using more
   than one Windows display. Use Desktop virtual only when the whole desktop is needed.
4. Open the phone URL and put it in the VR Box.
5. Adjust Lebar layar, Tinggi layar, and offsets until the visible area fits
   the lenses. Then tune barrel distortion and curvature in small steps.
6. Use keyboard/mouse or a controller connected to the PC as usual.

The stream frame only affects the image sent to the phone. To reduce the game's
own GPU workload, set the game's own resolution to `1280x720` or `1600x900` in its graphics menu. LensCast cannot force a game to render at 4:3; it captures the
pixels Windows receives. When a game offers 4:3, set it to `1280x960` or
`1440x1080`, then select the matching **4:3** stream preset. LensCast takes the
center 4:3 area before JPEG encoding, which removes only the side pillarbox and
keeps a genuine 4:3 game's UI intact. For a squarer lens area, `1280x1024` or
`1350x1080` plus the matching **5:4** preset works the same way.

If the game remains 16:9, leave **Area game dari monitor** on **Asli** and use
**Pas**. Selecting a 4:3/5:4 area while a 16:9 game is active intentionally crops
the left and right edges, including possible UI; it cannot make the same 16:9
picture larger without that tradeoff.

For the lowest practical latency, prefer the direct ADB USB tunnel. The phone
now requests only the newest completed frame, so it skips stale JPEGs rather
than displaying them late. A stable `40-50 fps` with JPEG quality around `50-65`
can feel more responsive than a congested 60 fps/very-high-quality stream. This
transport is designed for a virtual cinema view, so a regular game is shown
identically to both eyes; it does not add true 3D depth or headset tracking.

Once the `CAPTURE` readout stays at its target, raise Frame rate toward 60 before
raising stream resolution. Use `960x540` when Wi-Fi is the limit, then try
`1280x720`; `1600x900` can require much more bandwidth in detailed game scenes.
Keep 4:2:0 for latency first. Use 4:4:4 only when colored game text still looks
soft after selecting a suitable frame size, because it can substantially increase
the JPEG size and phone decode time.

Use Borderless Windowed or Windowed mode for Persona 3 Reload. Exclusive
fullscreen can prevent any desktop capture method from receiving the game frame
on some Windows/GPU combinations.

## Controls

| Control | Result on HP |
| --- | --- |
| Potong desktop | Chooses which part of the PC screen is mirrored. |
| Cara isi frame mata | `Pas` preserves the source with black bars; `Penuhi` crops; `Regang` changes aspect ratio. |
| Ukuran frame | Sets the stable encoded stream frame without changing the game's render resolution. |
| Area game dari monitor | Crops a centered 4:3 or 5:4 game area before resize/JPEG. Use only with a game already set to that same aspect ratio. |
| Detail warna dan teks | `4:2:0` minimizes latency; `4:4:4` keeps colored UI/text sharper at a higher bandwidth cost. |
| Tampilkan kursor PC | Draws a visible pointer into the captured frame when Windows/DXGI does not include it. |
| Lebar/Tinggi layar | Shrinks the active rectangle inside each physical half-screen. |
| Jarak antar mata | Moves the active rectangles apart to match the lens centers. |
| Offset horizontal | Moves the content outward or inward per eye. |
| Offset vertikal | Moves content up or down inside both eye rectangles. |
| Zoom virtual | Changes the apparent size of the desktop screen. |
| Barrel/Kelengkungan | Applies the GPU lens warp on the phone. |

## Verification

~~~powershell
python -m unittest discover -v
~~~

To rebuild the Android APK from a terminal after the one-time Android tool setup:

~~~powershell
cd "C:\Abie\Passion Project\VR\android"
.\gradlew.bat :app:assembleDebug --no-daemon
~~~

## Project map

~~~text
app.py                 Windows capture + local streaming server
static/studio.html     Desktop Studio interface
static/studio.js       Desktop preview and calibration controls
static/phone.html      Phone VR interface
static/mobile.js       WebGL dual-eye renderer
static/app.css         Shared visual design and responsive layout
android/               Native Android WebView wrapper and Gradle project
lenscast_pc.py         Native one-click Windows connection dashboard
tests/                 Settings validation tests
~~~
