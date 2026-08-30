# LensCast VR Studio

LensCast is a runnable MVP for watching a normal Windows desktop or PC game in a
phone VR Box. It mirrors the desktop to a phone over the local network, duplicates
the image into left/right eye views, and exposes manual calibration for different
VR Box and full-screen phone layouts.

This first version is a local Wi-Fi prototype. The server uses MJPEG so it can run
without Node, Android Studio, or a native phone install. The phone interface is a
web app at /phone; Chrome on Android can open it directly and use full-screen
landscape mode.

## What is included

- Live Windows desktop capture with a dependency-light Pillow/GDI fallback.
- Automatic DXGI Desktop Duplication capture for the 30 fps game profile, with
  Pillow/GDI fallback when DXGI is unavailable.
- OpenCV SIMD scaling on raw DXGI frames, with a four-worker cap so resize does
  not monopolize CPU cores needed by the game.
- A desktop control interface at /studio with an OBS-like live preview.
- Select the virtual desktop or a specific Windows monitor as the capture source.
- One-click quality profiles plus live bitrate, frame age, monitor, and headset status.
- Fixed stream-frame presets (`960x540`, `1280x720`, and `1600x900`) with
  automatic black letterboxing instead of stretched output.
- One shared settings model: changing a Studio slider updates the HP renderer.
- Independent control for source crop, eye width/height, eye gap, offsets, zoom,
  barrel distortion, curvature, brightness, and fit mode.
- Phone rendering through WebGL for two eye regions and lens distortion.
- A Canvas fallback for phones/browsers without WebGL.
- Persistent settings in lenscast-settings.json after the first run.

## Start

Double-click [Start LensCast.bat](<C:/Abie/Passion Project/VR/Start LensCast.bat>), or run:

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

## Practical setup for games

1. Start LensCast before starting the game.
2. For Persona 3 Reload, keep Mesin capture on Otomatis and use Game 30+
   first: DXGI, 36 fps target, `960x540` stream frame, and JPEG quality 58. The
   extra target headroom keeps measured FPS above 30 during Windows timing jitter.
   The capture note should say `DXGI Desktop Duplication aktif` and `OpenCV SIMD`.
   If it says Pillow instead, start LensCast through `Start LensCast.bat` so the
   Python 3.12 environment with the optimized dependencies is used.
3. Choose the monitor containing the game from Monitor sumber when using more
   than one Windows display. Use Desktop virtual only when the whole desktop is needed.
4. Open the phone URL and put it in the VR Box.
5. Adjust Lebar layar, Tinggi layar, and offsets until the visible area fits
   the lenses. Then tune barrel distortion and curvature in small steps.
6. Use keyboard/mouse or a controller connected to the PC as usual.

The stream frame only affects the image sent to the phone. To reduce the game's
own GPU workload, set the game's own resolution to `1280x720` or `1600x900` in
its graphics menu. Keep the game's aspect ratio at 16:9: LensCast duplicates that
one image into both eyes and adds black bars automatically when the VR Box lens
area is closer to a square.

For the lowest practical latency, use a dedicated 5 GHz/6 GHz Wi-Fi connection
with the PC wired to the router if possible. This transport is designed for a
virtual cinema view, so a regular game is shown identically to both eyes; it does
not add true 3D depth or headset tracking.

Once the `CAPTURE` readout stays at its target, raise Frame rate toward 60 before
raising stream resolution. Use `960x540` when Wi-Fi is the limit, then try
`1280x720`; `1600x900` can require much more bandwidth in detailed game scenes.

Use Borderless Windowed or Windowed mode for Persona 3 Reload. Exclusive
fullscreen can prevent any desktop capture method from receiving the game frame
on some Windows/GPU combinations.

## Controls

| Control | Result on HP |
| --- | --- |
| Potong desktop | Chooses which part of the PC screen is mirrored. |
| Cara isi frame mata | `Pas` preserves the source with black bars; `Penuhi` crops; `Regang` changes aspect ratio. |
| Ukuran frame | Sets the stable encoded stream frame without changing the game's render resolution. |
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

The project currently has no Android SDK or Java 17 installation on this PC, so
the phone prototype is intentionally delivered as a direct browser client. It is
the fastest path to validating VR Box fit and latency. A later native Android
wrapper can reuse the same /phone interface or replace the MJPEG transport with
WebRTC/H.264 without redesigning the Studio controls.

## Project map

~~~text
app.py                 Windows capture + local streaming server
static/studio.html     Desktop Studio interface
static/studio.js       Desktop preview and calibration controls
static/phone.html      Phone VR interface
static/mobile.js       WebGL dual-eye renderer
static/app.css         Shared visual design and responsive layout
tests/                 Settings validation tests
~~~
