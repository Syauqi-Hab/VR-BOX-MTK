#!/usr/bin/env python3
"""LensCast VR Studio - local desktop-to-phone VR mirroring MVP.

Run this file on Windows, open the Studio URL on the PC, then open the Phone
URL from a device on the same local network. The stream intentionally uses
MJPEG for a dependency-light first prototype; the client-side optics pipeline
is independent from the transport and can later move to WebRTC/H.264.
"""

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw, ImageGrab

try:
    import dxcam
except ImportError:
    dxcam = None

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
CONFIG_PATH = Path(os.environ.get("LENSCAST_CONFIG_PATH", APP_DIR / "lenscast-settings.json"))
LENS_PROFILES_PATH = Path(
    os.environ.get(
        "LENSCAST_LENS_PROFILES_PATH",
        CONFIG_PATH.with_name("lenscast-lens-profiles.json"),
    )
)
BOUNDARY = "lenscast-frame"
LATEST_FRAME_VIEWER_TTL_SECONDS = 4.0
DISPLAY_ID_PATTERN = re.compile(r"^\\\\\.\\DISPLAY\d+$", re.IGNORECASE)
CAPTURE_BACKENDS = {"auto", "dxgi", "pillow"}
SOURCE_FIT_MODES = {"contain", "cover", "stretch"}
MAX_LENS_PROFILES = 12
MAX_LENS_PROFILE_NAME_LENGTH = 32
HAS_OPENCV = cv2 is not None and np is not None

if HAS_OPENCV:
    # Four workers keep resize fast without briefly taking every CPU core from a game.
    cv2.setNumThreads(min(4, max(1, (os.cpu_count() or 1) // 2)))

DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "capture": {
        "display": "all",
        "backend": "auto",
        "fps": 50,
        "quality": 58,
        "scale": 0.5,  # Legacy setting retained for existing settings files.
        "cursor": True,
        "paused": False,
    },
    "stream": {
        "width": 960,
        "height": 540,
    },
    "source": {
        "cropX": 0,
        "cropY": 0,
        "cropWidth": 100,
        "cropHeight": 100,
        "fit": "contain",
    },
    "headset": {
        "eyeWidth": 92,
        "eyeHeight": 90,
        "eyeGap": 2,
        "eyeOffsetX": 0,
        "eyeOffsetY": 0,
        "zoom": 1.0,
        "barrel": 0.12,
        "curvature": 0.08,
        "brightness": 1.0,
        "nativeResolution": False,
    },
}

# Values are intentionally conservative. They keep malformed requests from
# making the phone shader produce unusable coordinates.
NUMERIC_LIMITS: dict[tuple[str, str], tuple[float, float, type]] = {
    ("capture", "fps"): (5, 60, int),
    ("capture", "quality"): (30, 95, int),
    ("capture", "scale"): (0.25, 1.0, float),
    ("stream", "width"): (320, 1920, int),
    ("stream", "height"): (180, 1080, int),
    ("source", "cropX"): (0, 95, float),
    ("source", "cropY"): (0, 95, float),
    ("source", "cropWidth"): (5, 100, float),
    ("source", "cropHeight"): (5, 100, float),
    ("headset", "eyeWidth"): (45, 100, float),
    ("headset", "eyeHeight"): (45, 100, float),
    ("headset", "eyeGap"): (0, 16, float),
    ("headset", "eyeOffsetX"): (-20, 20, float),
    ("headset", "eyeOffsetY"): (-20, 20, float),
    ("headset", "zoom"): (0.6, 2.2, float),
    ("headset", "barrel"): (-0.35, 0.5, float),
    ("headset", "curvature"): (-0.4, 0.4, float),
    ("headset", "brightness"): (0.5, 1.5, float),
}


def sanitize_settings(candidate: Any) -> dict[str, dict[str, Any]]:
    """Merge a settings payload into safe, complete settings."""
    result = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(candidate, dict):
        return result

    for group, defaults in DEFAULT_SETTINGS.items():
        values = candidate.get(group)
        if not isinstance(values, dict):
            continue
        for key, default_value in defaults.items():
            raw_value = values.get(key, default_value)
            if (group, key) == ("capture", "display"):
                if isinstance(raw_value, str):
                    display_id = raw_value.strip()
                    if display_id == "all":
                        result[group][key] = "all"
                    elif DISPLAY_ID_PATTERN.fullmatch(display_id):
                        result[group][key] = display_id.upper()
                continue
            if (group, key) == ("capture", "backend"):
                if isinstance(raw_value, str) and raw_value in CAPTURE_BACKENDS:
                    result[group][key] = raw_value
                continue
            if (group, key) == ("source", "fit"):
                if isinstance(raw_value, str) and raw_value in SOURCE_FIT_MODES:
                    result[group][key] = raw_value
                continue
            if (group, key) in {
                ("capture", "cursor"),
                ("capture", "paused"),
                ("headset", "nativeResolution"),
            }:
                if isinstance(raw_value, bool):
                    result[group][key] = raw_value
                continue
            limits = NUMERIC_LIMITS.get((group, key))
            if limits is None or isinstance(raw_value, bool):
                continue
            minimum, maximum, expected_type = limits
            if not isinstance(raw_value, (int, float)):
                continue
            value = min(maximum, max(minimum, float(raw_value)))
            if expected_type is int:
                integer_value = int(round(value))
                # Even dimensions keep the stream ready for future H.264 encoders.
                if group == "stream":
                    integer_value -= integer_value % 2
                result[group][key] = integer_value
            else:
                result[group][key] = round(value, 4)

    # Keep the crop rectangle inside the captured desktop.
    source = result["source"]
    source["cropWidth"] = min(source["cropWidth"], 100 - source["cropX"])
    source["cropHeight"] = min(source["cropHeight"], 100 - source["cropY"])
    return result


def read_saved_settings() -> dict[str, dict[str, Any]]:
    try:
        return sanitize_settings(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULT_SETTINGS)


def normalize_lens_profile_name(value: Any) -> str:
    """Return a readable, bounded profile name suitable for JSON storage."""
    if not isinstance(value, str):
        raise ValueError("Nama profil harus berupa teks.")
    name = " ".join(value.split())
    if not name:
        raise ValueError("Masukkan nama profil lensa.")
    if len(name) > MAX_LENS_PROFILE_NAME_LENGTH:
        raise ValueError(f"Nama profil maksimal {MAX_LENS_PROFILE_NAME_LENGTH} karakter.")
    if any(ord(character) < 32 for character in name):
        raise ValueError("Nama profil berisi karakter yang tidak valid.")
    return name


def sanitize_lens_profile(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("Data profil lensa tidak valid.")
    headset = candidate.get("headset")
    if not isinstance(headset, dict):
        raise ValueError("Profil lensa tidak memiliki data headset.")
    return {
        "name": normalize_lens_profile_name(candidate.get("name")),
        "headset": sanitize_settings({"headset": headset})["headset"],
    }


def read_saved_lens_profiles(path: Path = LENS_PROFILES_PATH) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []

    candidates = payload.get("profiles") if isinstance(payload, dict) else payload
    if not isinstance(candidates, list):
        return []

    profiles: list[dict[str, Any]] = []
    names: set[str] = set()
    for candidate in candidates:
        try:
            profile = sanitize_lens_profile(candidate)
        except ValueError:
            continue
        key = profile["name"].casefold()
        if key in names:
            continue
        names.add(key)
        profiles.append(profile)
        if len(profiles) >= MAX_LENS_PROFILES:
            break
    return profiles


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._settings = read_saved_settings()

    def get(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def update(self, patch: Any) -> dict[str, dict[str, Any]]:
        with self._lock:
            merged = copy.deepcopy(self._settings)
            if isinstance(patch, dict):
                for group in DEFAULT_SETTINGS:
                    values = patch.get(group)
                    if isinstance(values, dict):
                        merged[group].update(values)
            self._settings = sanitize_settings(merged)
            self._save_unlocked()
            return copy.deepcopy(self._settings)

    def reset(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._settings = copy.deepcopy(DEFAULT_SETTINGS)
            self._save_unlocked()
            return copy.deepcopy(self._settings)

    def _save_unlocked(self) -> None:
        try:
            temporary_path = CONFIG_PATH.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(self._settings, indent=2, ensure_ascii=True), encoding="utf-8"
            )
            os.replace(temporary_path, CONFIG_PATH)
        except OSError as error:
            print(f"Warning: settings could not be saved: {error}", file=sys.stderr)


class LensProfileStore:
    """Persist named headset-only calibrations independently from live settings."""

    def __init__(self, path: Path = LENS_PROFILES_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._profiles = read_saved_lens_profiles(path)

    def get(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._profiles)

    def save(self, name: Any, headset: Any) -> tuple[dict[str, Any], bool]:
        profile = sanitize_lens_profile({"name": name, "headset": headset})
        key = profile["name"].casefold()
        with self._lock:
            for index, existing in enumerate(self._profiles):
                if existing["name"].casefold() == key:
                    # Keep the original label so an update does not duplicate the profile.
                    profile["name"] = existing["name"]
                    self._profiles[index] = profile
                    self._save_unlocked()
                    return copy.deepcopy(profile), False
            if len(self._profiles) >= MAX_LENS_PROFILES:
                raise ValueError(f"Maksimal {MAX_LENS_PROFILES} profil lensa dapat disimpan.")
            self._profiles.append(profile)
            self._save_unlocked()
            return copy.deepcopy(profile), True

    def delete(self, name: Any) -> str:
        normalized_name = normalize_lens_profile_name(name)
        key = normalized_name.casefold()
        with self._lock:
            for index, profile in enumerate(self._profiles):
                if profile["name"].casefold() == key:
                    deleted_name = profile["name"]
                    del self._profiles[index]
                    self._save_unlocked()
                    return deleted_name
        raise KeyError(normalized_name)

    def _save_unlocked(self) -> None:
        try:
            temporary_path = self._path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps({"profiles": self._profiles}, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )
            os.replace(temporary_path, self._path)
        except OSError as error:
            print(f"Warning: lens profiles could not be saved: {error}", file=sys.stderr)


@dataclass(frozen=True)
class Frame:
    sequence: int
    jpeg: bytes
    width: int
    height: int
    captured_at: float


def placeholder_frame(message: str = "Preparing desktop capture") -> tuple[bytes, int, int]:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#11171a")
    draw = ImageDraw.Draw(image)
    for index in range(14):
        shade = 20 + index * 2
        draw.ellipse(
            (780 - index * 55, -180 - index * 35, 1320 + index * 55, 360 + index * 35),
            outline=(shade, 82 + index * 3, 76 + index * 2),
            width=4,
        )
    draw.rectangle((110, 110, 1170, 610), outline="#c8ff70", width=2)
    draw.text((154, 286), "LENSCAST", fill="#c8ff70")
    draw.text((154, 338), message, fill="#d8ded8")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue(), width, height


def is_raw_dxgi_frame(image: Any) -> bool:
    return HAS_OPENCV and isinstance(image, np.ndarray)


def image_dimensions(image: Image.Image | Any) -> tuple[int, int]:
    """Return image dimensions without converting a raw DXGI frame."""
    if is_raw_dxgi_frame(image):
        height, width = image.shape[:2]
        return int(width), int(height)
    return image.size


def fit_image_to_stream(
    image: Image.Image | Any,
    width: int,
    height: int,
    preserve_raw: bool = False,
) -> Image.Image | Any:
    """Resize to a stable stream frame without cropping or stretching the source."""
    raw_dxgi_frame = is_raw_dxgi_frame(image)
    if raw_dxgi_frame:
        source_height, source_width = image.shape[:2]
    else:
        source_width, source_height = image.size
    if source_width < 1 or source_height < 1:
        raise RuntimeError("Capture returned an empty image.")

    scale = min(width / source_width, height / source_height)
    resized_size = (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )
    if raw_dxgi_frame:
        downscaling = resized_size[0] <= source_width and resized_size[1] <= source_height
        interpolation = cv2.INTER_AREA if downscaling else cv2.INTER_LINEAR
        if (source_width, source_height) != resized_size:
            image = cv2.resize(image, resized_size, interpolation=interpolation)
        if preserve_raw and resized_size == (width, height) and image.ndim == 3 and image.shape[2] == 3:
            return image
        image = Image.fromarray(image)
    elif image.size != resized_size:
        horizontal_factor, horizontal_remainder = divmod(image.width, resized_size[0])
        vertical_factor, vertical_remainder = divmod(image.height, resized_size[1])
        if (
            horizontal_factor > 1
            and horizontal_factor == vertical_factor
            and horizontal_remainder == 0
            and vertical_remainder == 0
        ):
            # Image.reduce uses Pillow's optimized box filter for exact integer downscales.
            image = image.reduce(horizontal_factor)
        else:
            downscaling = resized_size[0] <= image.width and resized_size[1] <= image.height
            resample = Image.Resampling.BOX if downscaling else Image.Resampling.BILINEAR
            image = image.resize(resized_size, resample)
    if image.mode != "RGB":
        image = image.convert("RGB")

    if image.size == (width, height):
        return image

    frame = Image.new("RGB", (width, height), "black")
    frame.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
    return frame


def windows_cursor_position() -> tuple[int, int] | None:
    """Read the Windows pointer position in physical desktop pixels."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        position = Point()
        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
        user32.GetCursorPos.restype = ctypes.c_int
        if not user32.GetCursorPos(ctypes.byref(position)):
            return None
        return int(position.x), int(position.y)
    except Exception:
        return None


def project_cursor_to_stream(
    cursor_position: tuple[int, int] | None,
    selected_display: dict[str, Any],
    source_size: tuple[int, int],
    stream_size: tuple[int, int],
) -> tuple[int, int] | None:
    """Map a Windows cursor point into the letterboxed encoded stream frame."""
    if cursor_position is None:
        return None
    try:
        display_left = int(selected_display["x"])
        display_top = int(selected_display["y"])
        display_width = int(selected_display["width"])
        display_height = int(selected_display["height"])
    except (KeyError, TypeError, ValueError):
        return None
    source_width, source_height = source_size
    stream_width, stream_height = stream_size
    if min(display_width, display_height, source_width, source_height, stream_width, stream_height) < 1:
        return None

    relative_x = cursor_position[0] - display_left
    relative_y = cursor_position[1] - display_top
    if not (0 <= relative_x < display_width and 0 <= relative_y < display_height):
        return None

    source_x = relative_x * source_width / display_width
    source_y = relative_y * source_height / display_height
    scale = min(stream_width / source_width, stream_height / source_height)
    rendered_width = max(1, round(source_width * scale))
    rendered_height = max(1, round(source_height * scale))
    left = (stream_width - rendered_width) // 2
    top = (stream_height - rendered_height) // 2
    return (
        min(stream_width - 1, max(0, left + round(source_x * scale))),
        min(stream_height - 1, max(0, top + round(source_y * scale))),
    )


def overlay_cursor_marker(
    image: Image.Image | Any,
    cursor_position: tuple[int, int] | None,
) -> Image.Image | Any:
    """Draw a high-contrast pointer so DXGI/GDI captures include the mouse."""
    if cursor_position is None:
        return image
    width, height = image_dimensions(image)
    x, y = cursor_position
    marker_size = max(16, min(50, round(min(width, height) * 0.055)))
    if x < -marker_size or y < -marker_size or x >= width or y >= height:
        return image

    points = [
        (x, y),
        (x, y + marker_size),
        (x + round(marker_size * 0.25), y + round(marker_size * 0.72)),
        (x + round(marker_size * 0.46), y + marker_size),
        (x + round(marker_size * 0.64), y + round(marker_size * 0.87)),
        (x + round(marker_size * 0.41), y + round(marker_size * 0.58)),
        (x + round(marker_size * 0.80), y + round(marker_size * 0.58)),
    ]
    outline_width = max(2, round(marker_size * 0.1))
    if is_raw_dxgi_frame(image):
        if not image.flags.writeable:
            image = image.copy()
        vertices = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(image, [vertices], color=(250, 250, 250), lineType=cv2.LINE_AA)
        cv2.polylines(image, [vertices], True, color=(0, 0, 0), thickness=outline_width, lineType=cv2.LINE_AA)
        return image

    draw = ImageDraw.Draw(image)
    draw.polygon(points, fill=(250, 250, 250))
    draw.line([*points, points[0]], fill=(0, 0, 0), width=outline_width)
    return image


def encode_stream_frame(image: Image.Image | Any, quality: int) -> bytes:
    """Encode raw DXGI RGB with OpenCV, retaining Pillow for every fallback path."""
    if is_raw_dxgi_frame(image):
        if image.ndim == 3 and image.shape[2] == 3:
            bgr_image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            success, encoded = cv2.imencode(
                ".jpg",
                bgr_image,
                [cv2.IMWRITE_JPEG_QUALITY, quality],
            )
            if success:
                return encoded.tobytes()
        image = Image.fromarray(image)

    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=False,
        subsampling=2,
    )
    return buffer.getvalue()


def configure_dpi_awareness() -> None:
    """Keep monitor coordinates aligned with the pixels returned by screen capture."""
    if os.name != "nt":
        return
    try:
        import ctypes

        # Per-monitor awareness is preferable on mixed-DPI desktop setups.
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def virtual_desktop_bounds() -> tuple[int, int, int, int]:
    """Return Windows virtual desktop coordinates, including negative monitor origins."""
    if os.name != "nt":
        return (0, 0, 0, 0)
    try:
        import ctypes

        get_metric = ctypes.windll.user32.GetSystemMetrics
        get_metric.argtypes = [ctypes.c_int]
        get_metric.restype = ctypes.c_int
        return (
            get_metric(76),  # SM_XVIRTUALSCREEN
            get_metric(77),  # SM_YVIRTUALSCREEN
            get_metric(78),  # SM_CXVIRTUALSCREEN
            get_metric(79),  # SM_CYVIRTUALSCREEN
        )
    except Exception:
        return (0, 0, 0, 0)


def enumerate_windows_displays() -> list[dict[str, Any]]:
    """List physical Windows monitors plus an all-monitors virtual desktop target."""
    left, top, width, height = virtual_desktop_bounds()
    virtual_display: dict[str, Any] = {
        "id": "all",
        "label": "Desktop virtual / semua monitor",
        "x": left,
        "y": top,
        "width": width,
        "height": height,
        "primary": False,
    }
    if os.name != "nt":
        return [virtual_display]

    try:
        import ctypes

        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MonitorInfoEx(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint32),
                ("rcMonitor", Rect),
                ("rcWork", Rect),
                ("dwFlags", ctypes.c_uint32),
                ("szDevice", ctypes.c_wchar * 32),
            ]

        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(Rect),
            ctypes.c_ssize_t,
        )
        user32.EnumDisplayMonitors.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            callback_type,
            ctypes.c_ssize_t,
        ]
        user32.EnumDisplayMonitors.restype = ctypes.c_int
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfoEx)]
        user32.GetMonitorInfoW.restype = ctypes.c_int
        monitors: list[dict[str, Any]] = []

        @callback_type
        def collect(monitor: Any, _device_context: Any, _rect: Any, _data: Any) -> int:
            info = MonitorInfoEx()
            info.cbSize = ctypes.sizeof(MonitorInfoEx)
            if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                bounds = info.rcMonitor
                monitors.append(
                    {
                        "id": info.szDevice.upper(),
                        "label": "",
                        "x": int(bounds.left),
                        "y": int(bounds.top),
                        "width": int(bounds.right - bounds.left),
                        "height": int(bounds.bottom - bounds.top),
                        "primary": bool(info.dwFlags & 1),
                    }
                )
            return 1

        if not user32.EnumDisplayMonitors(None, None, collect, 0):
            return [virtual_display]
        monitors.sort(key=lambda monitor: (not monitor["primary"], monitor["y"], monitor["x"]))
        for index, monitor in enumerate(monitors, start=1):
            primary_suffix = " (utama)" if monitor["primary"] else ""
            monitor["label"] = (
                f"Monitor {index}{primary_suffix} - {monitor['width']} x {monitor['height']}"
            )
        return [virtual_display, *monitors]
    except Exception:
        return [virtual_display]


class DisplayCatalog:
    """Caches monitor metadata so the capture loop does not enumerate each frame."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._displays = enumerate_windows_displays()
        self._last_refresh = time.monotonic()

    def get(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            if force_refresh or time.monotonic() - self._last_refresh > 3:
                self._displays = enumerate_windows_displays()
                self._last_refresh = time.monotonic()
            return copy.deepcopy(self._displays)

    def resolve(self, display_id: str) -> tuple[dict[str, Any], bool]:
        displays = self.get()
        for display in displays:
            if display["id"] == display_id:
                return display, True
        return displays[0], display_id == "all"


def crop_image_to_display(
    image: Image.Image,
    virtual_display: dict[str, Any],
    selected_display: dict[str, Any],
) -> Image.Image:
    """Crop a full virtual-desktop screenshot to a selected physical monitor."""
    if selected_display["id"] == "all":
        return image

    virtual_width = max(1, int(virtual_display["width"]))
    virtual_height = max(1, int(virtual_display["height"]))
    scale_x = image.width / virtual_width
    scale_y = image.height / virtual_height
    left = round((selected_display["x"] - virtual_display["x"]) * scale_x)
    top = round((selected_display["y"] - virtual_display["y"]) * scale_y)
    right = round(left + selected_display["width"] * scale_x)
    bottom = round(top + selected_display["height"] * scale_y)
    left = min(max(left, 0), image.width)
    top = min(max(top, 0), image.height)
    right = min(max(right, 0), image.width)
    bottom = min(max(bottom, 0), image.height)
    if right <= left or bottom <= top:
        raise RuntimeError("Selected monitor is outside the captured virtual desktop.")
    return image.crop((left, top, right, bottom))


def dxgi_output_index(
    selected_display: dict[str, Any],
    displays: list[dict[str, Any]],
) -> int | None:
    """Map a catalogued monitor to dxcam's output index on the primary adapter."""
    physical_displays = [display for display in displays if display["id"] != "all"]
    if selected_display["id"] == "all":
        return 0 if len(physical_displays) == 1 else None
    for index, display in enumerate(physical_displays):
        if display["id"] == selected_display["id"]:
            return index
    return None


class DxgiDesktopCapture:
    """Small stateful wrapper around dxcam's Desktop Duplication API."""

    def __init__(self) -> None:
        self._camera: Any | None = None
        self._camera_key: tuple[int, int] | None = None

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
        self._camera = None
        self._camera_key = None

    def grab(
        self,
        selected_display: dict[str, Any],
        displays: list[dict[str, Any]],
    ) -> Image.Image | Any:
        if dxcam is None:
            raise RuntimeError("dxcam is not installed.")
        output_index = dxgi_output_index(selected_display, displays)
        if output_index is None:
            raise RuntimeError(
                "DXGI captures one monitor at a time. Select a monitor instead of a multi-monitor desktop."
            )
        camera_key = (0, output_index)
        try:
            if self._camera is None or self._camera_key != camera_key:
                self.close()
                self._camera = dxcam.create(
                    device_idx=camera_key[0],
                    output_idx=camera_key[1],
                    output_color="RGB",
                    processor_backend="numpy",
                )
                self._camera_key = camera_key
            frame = self._camera.grab(new_frame_only=False, copy=False)
            if frame is None:
                raise RuntimeError("DXGI did not return a desktop frame.")
            return frame if HAS_OPENCV else Image.fromarray(frame)
        except Exception:
            self.close()
            raise


def grab_desktop_with_gdi() -> Image.Image:
    """Capture the Windows virtual desktop without depending on ImageGrab internals."""
    if os.name != "nt":
        raise RuntimeError("The GDI fallback is only available on Windows.")

    import ctypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    handle = ctypes.c_void_p

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    class RgbQuad(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", ctypes.c_ubyte),
            ("rgbGreen", ctypes.c_ubyte),
            ("rgbRed", ctypes.c_ubyte),
            ("rgbReserved", ctypes.c_ubyte),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", RgbQuad * 1)]

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [handle]
    user32.GetDC.restype = handle
    user32.ReleaseDC.argtypes = [handle, handle]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [handle]
    gdi32.CreateCompatibleDC.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.SelectObject.argtypes = [handle, handle]
    gdi32.SelectObject.restype = handle
    gdi32.BitBlt.argtypes = [
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        handle,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    gdi32.BitBlt.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [
        handle,
        handle,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(BitmapInfo),
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.DeleteDC.argtypes = [handle]
    gdi32.DeleteDC.restype = ctypes.c_int

    # SM_XVIRTUALSCREEN through SM_CYVIRTUALSCREEN include all attached monitors.
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    if width < 1 or height < 1:
        raise RuntimeError("Windows reported an empty virtual desktop.")

    desktop_dc = user32.GetDC(None)
    memory_dc = handle()
    bitmap = handle()
    previous_object = handle()
    try:
        if not desktop_dc:
            raise ctypes.WinError()
        memory_dc = gdi32.CreateCompatibleDC(desktop_dc)
        bitmap = gdi32.CreateCompatibleBitmap(desktop_dc, width, height)
        if not memory_dc or not bitmap:
            raise ctypes.WinError()
        previous_object = gdi32.SelectObject(memory_dc, bitmap)
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            desktop_dc,
            left,
            top,
            0x00CC0020 | 0x40000000,  # SRCCOPY | CAPTUREBLT
        ):
            raise ctypes.WinError()

        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # Top-down DIB, matching screen coordinates.
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        pixels = (ctypes.c_ubyte * (width * height * 4))()
        copied_rows = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            pixels,
            ctypes.byref(info),
            0,  # DIB_RGB_COLORS
        )
        if copied_rows != height:
            raise ctypes.WinError()
        return Image.frombytes("RGB", (width, height), bytes(pixels), "raw", "BGRX", 0, 1)
    finally:
        if memory_dc and previous_object:
            gdi32.SelectObject(memory_dc, previous_object)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if desktop_dc:
            user32.ReleaseDC(None, desktop_dc)


def grab_desktop() -> Image.Image:
    """Prefer Pillow, then use direct GDI if a specific Pillow build fails."""
    try:
        return ImageGrab.grab(all_screens=True)
    except Exception as pillow_error:
        try:
            return grab_desktop_with_gdi()
        except Exception as gdi_error:
            raise RuntimeError(
                f"Pillow capture failed ({pillow_error}); GDI capture failed ({gdi_error})"
            ) from gdi_error


class DesktopCapture:
    def __init__(self, settings: SettingsStore, displays: DisplayCatalog) -> None:
        jpeg, width, height = placeholder_frame()
        self._settings = settings
        self._displays = displays
        self._dxgi = DxgiDesktopCapture()
        self._condition = threading.Condition()
        self._frame = Frame(0, jpeg, width, height, time.monotonic())
        self._running = threading.Event()
        self._running.set()
        self._error = ""
        self._captured_frames = 0
        self._encoded_bytes = 0
        self._fps_window_started = time.monotonic()
        self._measured_fps = 0.0
        self._bitrate_mbps = 0.0
        self._target_fps = DEFAULT_SETTINGS["capture"]["fps"]
        self._timing_frames = 0
        self._capture_ms_total = 0.0
        self._resize_ms_total = 0.0
        self._encode_ms_total = 0.0
        self._pipeline_ms_total = 0.0
        self._capture_ms = 0.0
        self._resize_ms = 0.0
        self._encode_ms = 0.0
        self._pipeline_ms = 0.0
        self._target_id = "all"
        self._target_label = "Desktop virtual / semua monitor"
        self._target_available = True
        self._requested_backend = "auto"
        self._active_backend = "starting"
        self._backend_detail = "Memilih backend capture."
        self._dxgi_retry_at = 0.0
        self._thread = threading.Thread(target=self._capture_loop, name="desktop-capture", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        self._thread.join(timeout=2)
        self._dxgi.close()

    def get_after(self, sequence: int, timeout: float = 4.0) -> Frame:
        with self._condition:
            if self._frame.sequence <= sequence:
                self._condition.wait_for(lambda: self._frame.sequence > sequence, timeout=timeout)
            return self._frame

    def status(self) -> dict[str, Any]:
        with self._condition:
            frame = self._frame
            return {
                "sequence": frame.sequence,
                "width": frame.width,
                "height": frame.height,
                "ageMs": round((time.monotonic() - frame.captured_at) * 1000),
                "measuredFps": round(self._measured_fps, 1),
                "targetFps": self._target_fps,
                "bitrateMbps": round(self._bitrate_mbps, 2),
                "frameBytes": len(frame.jpeg),
                "timings": {
                    "captureMs": round(self._capture_ms, 1),
                    "resizeMs": round(self._resize_ms, 1),
                    "encodeMs": round(self._encode_ms, 1),
                    "pipelineMs": round(self._pipeline_ms, 1),
                },
                "target": {
                    "id": self._target_id,
                    "label": self._target_label,
                    "available": self._target_available,
                },
                "backend": {
                    "requested": self._requested_backend,
                    "active": self._active_backend,
                    "detail": self._backend_detail,
                },
                "error": self._error,
            }

    def _capture_image(
        self,
        capture_settings: dict[str, Any],
    ) -> tuple[Image.Image, dict[str, Any], bool, str, str]:
        selected_display, target_available = self._displays.resolve(capture_settings["display"])
        displays = self._displays.get()
        requested_backend = capture_settings["backend"]
        dxgi_error = ""

        if requested_backend in {"auto", "dxgi"} and time.monotonic() >= self._dxgi_retry_at:
            try:
                image = self._dxgi.grab(selected_display, displays)
                return (
                    image,
                    selected_display,
                    target_available,
                    "dxgi",
                    "DXGI Desktop Duplication aktif.",
                )
            except Exception as error:
                self._dxgi_retry_at = time.monotonic() + 3
                dxgi_error = str(error)
        elif requested_backend == "pillow":
            self._dxgi.close()

        image = grab_desktop()
        virtual_display = displays[0]
        if virtual_display["width"] < 1 or virtual_display["height"] < 1:
            virtual_display = {
                **virtual_display,
                "x": 0,
                "y": 0,
                "width": image.width,
                "height": image.height,
            }
        image = crop_image_to_display(image, virtual_display, selected_display)
        detail = "Pillow ImageGrab aktif."
        if dxgi_error:
            detail = "DXGI fallback ke Pillow: " + dxgi_error
        elif requested_backend == "dxgi":
            detail = "DXGI sedang cooldown; Pillow dipakai sementara."
        return image, selected_display, target_available, "pillow", detail

    def _capture_loop(self) -> None:
        next_frame_at = time.monotonic()
        while self._running.is_set():
            current_settings = self._settings.get()
            capture_settings = current_settings["capture"]
            stream_settings = current_settings["stream"]
            if capture_settings["paused"]:
                time.sleep(0.12)
                next_frame_at = time.monotonic()
                continue

            try:
                captured_at = time.monotonic()
                frame_started_at = time.perf_counter()
                (
                    image,
                    selected_display,
                    target_available,
                    active_backend,
                    backend_detail,
                ) = self._capture_image(capture_settings)
                capture_finished_at = time.perf_counter()
                raw_dxgi_frame = is_raw_dxgi_frame(image)
                source_size = image_dimensions(image)
                cursor_position = (
                    windows_cursor_position() if capture_settings["cursor"] else None
                )
                image = fit_image_to_stream(
                    image,
                    stream_settings["width"],
                    stream_settings["height"],
                    preserve_raw=raw_dxgi_frame,
                )
                image = overlay_cursor_marker(
                    image,
                    project_cursor_to_stream(
                        cursor_position,
                        selected_display,
                        source_size,
                        (stream_settings["width"], stream_settings["height"]),
                    ),
                )
                resize_finished_at = time.perf_counter()
                if is_raw_dxgi_frame(image):
                    backend_detail += " OpenCV SIMD resize dan JPEG aktif."
                elif raw_dxgi_frame:
                    backend_detail += " OpenCV SIMD resize aktif; Pillow JPEG dipakai untuk letterbox."
                encoded_frame = encode_stream_frame(image, capture_settings["quality"])
                encode_finished_at = time.perf_counter()
                now = time.monotonic()
                capture_ms = (capture_finished_at - frame_started_at) * 1000
                resize_ms = (resize_finished_at - capture_finished_at) * 1000
                encode_ms = (encode_finished_at - resize_finished_at) * 1000
                pipeline_ms = (encode_finished_at - frame_started_at) * 1000
                if is_raw_dxgi_frame(image):
                    image_height, image_width = image.shape[:2]
                else:
                    image_width, image_height = image.size
                with self._condition:
                    self._frame = Frame(
                        self._frame.sequence + 1,
                        encoded_frame,
                        image_width,
                        image_height,
                        captured_at,
                    )
                    self._error = ""
                    self._target_id = selected_display["id"]
                    self._target_label = selected_display["label"]
                    self._target_available = target_available
                    self._requested_backend = capture_settings["backend"]
                    self._active_backend = active_backend
                    self._backend_detail = backend_detail
                    self._captured_frames += 1
                    self._encoded_bytes += len(encoded_frame)
                    self._target_fps = capture_settings["fps"]
                    self._timing_frames += 1
                    self._capture_ms_total += capture_ms
                    self._resize_ms_total += resize_ms
                    self._encode_ms_total += encode_ms
                    self._pipeline_ms_total += pipeline_ms
                    elapsed = now - self._fps_window_started
                    if elapsed >= 1.0:
                        self._measured_fps = self._captured_frames / elapsed
                        self._bitrate_mbps = self._encoded_bytes * 8 / elapsed / 1_000_000
                        timing_frames = max(1, self._timing_frames)
                        self._capture_ms = self._capture_ms_total / timing_frames
                        self._resize_ms = self._resize_ms_total / timing_frames
                        self._encode_ms = self._encode_ms_total / timing_frames
                        self._pipeline_ms = self._pipeline_ms_total / timing_frames
                        self._captured_frames = 0
                        self._encoded_bytes = 0
                        self._timing_frames = 0
                        self._capture_ms_total = 0.0
                        self._resize_ms_total = 0.0
                        self._encode_ms_total = 0.0
                        self._pipeline_ms_total = 0.0
                        self._fps_window_started = now
                    self._condition.notify_all()
            except Exception as error:  # ImageGrab errors should not kill the server.
                with self._condition:
                    self._error = str(error)
                time.sleep(0.5)

            interval = 1 / capture_settings["fps"]
            next_frame_at += interval
            delay = next_frame_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1:
                next_frame_at = time.monotonic()


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
    except socket.gaierror:
        pass

    return [address for address in addresses if not address.startswith("127.")]


class ViewerRegistry:
    """Tracks stream readers and short-poll latest-frame clients for Studio status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = {"studio": 0, "phone": 0, "other": 0}
        self._last_seen: dict[str, float] = {}

    def connect(self, requested_role: str) -> str:
        role = requested_role if requested_role in self._counts else "other"
        with self._lock:
            self._counts[role] += 1
        return role

    def disconnect(self, role: str) -> None:
        with self._lock:
            self._counts[role] = max(0, self._counts.get(role, 0) - 1)

    def heartbeat(self, requested_role: str) -> str:
        """Keep a latest-frame client visible while it long-polls for fresh JPEGs."""
        role = requested_role if requested_role in self._counts else "other"
        with self._lock:
            self._last_seen[role] = time.monotonic()
        return role

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            snapshot = dict(self._counts)
            cutoff = time.monotonic() - LATEST_FRAME_VIEWER_TTL_SECONDS
            for role, seen_at in tuple(self._last_seen.items()):
                if seen_at < cutoff:
                    del self._last_seen[role]
                else:
                    snapshot[role] = max(snapshot[role], 1)
            return snapshot


def adb_connected_serials(output: str) -> list[str]:
    """Return only authorized ADB device serials from `adb devices` output."""
    return [
        line.split("\t", 1)[0]
        for line in output.splitlines()
        if "\tdevice" in line
    ]


def find_adb_path() -> str | None:
    """Find ADB from PATH or the standard Android SDK location on Windows."""
    executable = "adb.exe" if os.name == "nt" else "adb"
    candidates = [shutil.which("adb")]
    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk_root:
        candidates.append(str(Path(sdk_root) / "platform-tools" / executable))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(str(Path(local_app_data) / "Android" / "Sdk" / "platform-tools" / executable))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def hidden_subprocess_kwargs() -> dict[str, int]:
    """Prevent short-lived Windows console windows from ADB maintenance calls."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


class AdbReverseKeeper:
    """Restores the ADB reverse rule if Android restarts its USB transport."""

    def __init__(self, port: int, interval_seconds: float = 3.0) -> None:
        self._adb_path = find_adb_path()
        self._port = port
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_state = ""

    def start(self) -> None:
        if not self._adb_path:
            print("ADB USB tunnel tidak aktif: adb tidak ditemukan.", file=sys.stderr)
            return
        self._thread = threading.Thread(
            target=self._maintain,
            name="lenscast-adb-reverse",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _maintain(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = subprocess.run(
                    [self._adb_path, "devices"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                serials = adb_connected_serials(result.stdout)
                if not serials:
                    self._report_state("menunggu HP dengan USB debugging")
                else:
                    restored = all(self._restore_for(serial) for serial in serials)
                    self._report_state("aktif" if restored else "gagal, akan mencoba lagi")
            except (OSError, subprocess.SubprocessError):
                self._report_state("gagal, akan mencoba lagi")
            self._stop_event.wait(self._interval_seconds)

    def _restore_for(self, serial: str) -> bool:
        result = subprocess.run(
            [
                self._adb_path,
                "-s",
                serial,
                "reverse",
                f"tcp:{self._port}",
                f"tcp:{self._port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return result.returncode == 0

    def _report_state(self, state: str) -> None:
        if state != self._last_state:
            self._last_state = state
            print(f"ADB USB tunnel: {state}.")


class LensCastHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        settings: SettingsStore,
        lens_profiles: LensProfileStore,
        capture: DesktopCapture,
        displays: DisplayCatalog,
        viewers: ViewerRegistry,
    ) -> None:
        super().__init__(address, LensCastRequestHandler)
        self.settings = settings
        self.lens_profiles = lens_profiles
        self.capture = capture
        self.displays = displays
        self.viewers = viewers


class LensCastRequestHandler(BaseHTTPRequestHandler):
    server: LensCastHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        # Keep the console useful while still exposing request failures.
        if self.command != "GET" or not self.path.startswith(("/stream.mjpg", "/frame.jpg")):
            print(f"[{self.log_date_time_string()}] {format_string % args}")

    def do_GET(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        path = request.path
        if path == "/":
            self._redirect("/studio")
        elif path == "/studio":
            self._serve_static("studio.html")
        elif path == "/phone":
            self._serve_static("phone.html")
        elif path == "/api/settings":
            self._json_response(self.server.settings.get())
        elif path == "/api/lens-profiles":
            self._json_response({"profiles": self.server.lens_profiles.get()})
        elif path == "/api/status":
            self._json_response(self._status_payload())
        elif path == "/api/displays":
            self._json_response({"displays": self.server.displays.get(force_refresh=True)})
        elif path == "/api/health":
            self._json_response({"ok": True})
        elif path == "/frame.jpg":
            self._serve_latest_frame(request.query)
        elif path == "/stream.mjpg":
            self._serve_mjpeg(request.query)
        elif path.startswith("/assets/"):
            self._serve_static(path.removeprefix("/assets/"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Route not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/settings":
            payload = self._read_json_body()
            if payload is None:
                return
            self._json_response(self.server.settings.update(payload))
        elif path == "/api/lens-profiles":
            payload = self._read_json_body()
            if payload is None:
                return
            if not isinstance(payload, dict):
                self.send_error(HTTPStatus.BAD_REQUEST, "Data profil lensa tidak valid.")
                return
            try:
                profile, created = self.server.lens_profiles.save(
                    payload.get("name"), payload.get("headset")
                )
            except ValueError as error:
                self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            self._json_response(
                {
                    "profiles": self.server.lens_profiles.get(),
                    "saved": profile["name"],
                    "created": created,
                }
            )
        elif path == "/api/reset":
            self._json_response(self.server.settings.reset())
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Route not found")

    def do_DELETE(self) -> None:  # noqa: N802
        request = urlparse(self.path)
        if request.path != "/api/lens-profiles":
            self.send_error(HTTPStatus.NOT_FOUND, "Route not found")
            return
        names = parse_qs(request.query).get("name", [])
        if len(names) != 1:
            self.send_error(HTTPStatus.BAD_REQUEST, "Pilih profil lensa yang akan dihapus.")
            return
        try:
            deleted_name = self.server.lens_profiles.delete(names[0])
        except ValueError as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except KeyError:
            self.send_error(HTTPStatus.NOT_FOUND, "Profil lensa tidak ditemukan.")
            return
        self._json_response(
            {"profiles": self.server.lens_profiles.get(), "deleted": deleted_name}
        )

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self) -> Any | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("Request body must be between 1 and 65536 bytes.")
            raw_body = self.rfile.read(length)
            return json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            self.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return None

    def _status_payload(self) -> dict[str, Any]:
        port = self.server.server_address[1]
        addresses = local_ipv4_addresses()
        phone_urls = [f"http://{address}:{port}/phone" for address in addresses]
        return {
            "capture": self.server.capture.status(),
            "viewers": self.server.viewers.snapshot(),
            "phoneUrls": phone_urls,
            "studioUrl": f"http://127.0.0.1:{port}/studio",
        }

    def _json_response(self, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, destination: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", destination)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_static(self, filename: str) -> None:
        requested = (STATIC_DIR / filename).resolve()
        try:
            requested.relative_to(STATIC_DIR.resolve())
            if not requested.is_file():
                raise FileNotFoundError
        except (ValueError, FileNotFoundError):
            self.send_error(HTTPStatus.NOT_FOUND, "Static file not found")
            return

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }.get(requested.suffix.lower(), "application/octet-stream")
        try:
            body = requested.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not read static file")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _configure_stream_socket(self, send_buffer_bytes: int) -> None:
        try:
            # Bound in-flight data. Latest-frame delivery cannot queue a second JPEG by design.
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, send_buffer_bytes)
            self.connection.settimeout(2.0)
        except OSError:
            pass

    @staticmethod
    def _after_sequence(query: str) -> int:
        raw_value = parse_qs(query).get("after", ["-1"])[0]
        try:
            return max(-1, min(int(raw_value), 2_147_483_647))
        except (TypeError, ValueError):
            return -1

    def _serve_latest_frame(self, query: str) -> None:
        """Return one fresh JPEG, waiting briefly instead of letting the client queue MJPEG parts."""
        self._configure_stream_socket(256 * 1024)
        requested_role = parse_qs(query).get("role", ["other"])[0]
        after_sequence = self._after_sequence(query)
        frame = self.server.capture.get_after(after_sequence, timeout=1.2)
        self.server.viewers.heartbeat(requested_role)
        if frame.sequence <= after_sequence:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        frame_age_ms = max(0, round((time.monotonic() - frame.captured_at) * 1000))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame.jpeg)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-LensCast-Sequence", str(frame.sequence))
        self.send_header("X-LensCast-Frame-Age-Ms", str(frame_age_ms))
        self.end_headers()
        try:
            self.wfile.write(frame.jpeg)
            self.wfile.flush()
        except OSError:
            pass

    def _serve_mjpeg(self, query: str) -> None:
        self._configure_stream_socket(64 * 1024)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        requested_role = parse_qs(query).get("role", ["other"])[0]
        viewer_role = self.server.viewers.connect(requested_role)
        sequence = -1
        try:
            while True:
                frame = self.server.capture.get_after(sequence)
                sequence = frame.sequence
                part = b"".join(
                    (
                        f"--{BOUNDARY}\r\n".encode("ascii"),
                        b"Content-Type: image/jpeg\r\n",
                        f"Content-Length: {len(frame.jpeg)}\r\n\r\n".encode("ascii"),
                        frame.jpeg,
                        b"\r\n",
                    )
                )
                self.wfile.write(part)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.server.viewers.disconnect(viewer_role)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LensCast VR Studio local streaming server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: loopback only)")
    parser.add_argument("--port", default=8264, type=int, help="HTTP port (default: 8264)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the Studio page automatically")
    parser.add_argument(
        "--adb-reverse",
        action="store_true",
        help="Keep an ADB USB reverse rule active for the selected port",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    if not STATIC_DIR.is_dir():
        print(f"Static folder missing: {STATIC_DIR}", file=sys.stderr)
        return 1

    configure_dpi_awareness()
    settings = SettingsStore()
    lens_profiles = LensProfileStore()
    displays = DisplayCatalog()
    viewers = ViewerRegistry()
    capture = DesktopCapture(settings, displays)
    capture.start()
    try:
        server = LensCastHTTPServer(
            (args.host, args.port),
            settings,
            lens_profiles,
            capture,
            displays,
            viewers,
        )
    except OSError as error:
        capture.stop()
        print(f"Could not start LensCast on port {args.port}: {error}", file=sys.stderr)
        return 1

    adb_reverse = AdbReverseKeeper(server.server_address[1]) if args.adb_reverse else None
    if adb_reverse is not None:
        adb_reverse.start()

    local_url = f"http://127.0.0.1:{server.server_address[1]}/studio"
    print("\nLensCast VR Studio is running.")
    print(f"Studio: {local_url}")
    if args.host in {"127.0.0.1", "localhost"}:
        print(f"Phone:  USB ADB -> http://127.0.0.1:{server.server_address[1]}/phone")
    else:
        phone_urls = [f"http://{address}:{server.server_address[1]}/phone" for address in local_ipv4_addresses()]
        if phone_urls:
            print("Phone:  " + phone_urls[0])
        else:
            print("Phone:  use this PC's LAN IPv4 address with /phone")
    print("Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(local_url)).start()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping LensCast...")
    finally:
        server.shutdown()
        server.server_close()
        capture.stop()
        if adb_reverse is not None:
            adb_reverse.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
