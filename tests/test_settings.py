import http.client
import io
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from app import (
    DEFAULT_SETTINGS,
    Frame,
    LensCastHTTPServer,
    LensProfileStore,
    ViewerRegistry,
    adb_connected_serials,
    crop_image_to_display,
    dxgi_output_index,
    encode_stream_frame,
    fit_image_to_stream,
    hidden_subprocess_kwargs,
    overlay_cursor_marker,
    parse_arguments,
    project_cursor_to_stream,
    sanitize_settings,
)


class SettingsValidationTests(unittest.TestCase):
    def test_adb_subprocesses_hide_console_windows_on_windows(self):
        kwargs = hidden_subprocess_kwargs()
        if os.name == "nt":
            self.assertEqual(kwargs, {"creationflags": subprocess.CREATE_NO_WINDOW})
        else:
            self.assertEqual(kwargs, {})

    def test_server_defaults_to_loopback_only(self):
        with patch("sys.argv", ["app.py"]):
            self.assertEqual(parse_arguments().host, "127.0.0.1")

    def test_adb_connected_serials_ignores_offline_and_unauthorized_devices(self):
        output = """List of devices attached
ready\tdevice product:vivo_1910
offline\toffline transport_id:2
locked\tunauthorized usb:1-4
"""
        self.assertEqual(adb_connected_serials(output), ["ready"])

    def test_defaults_are_complete_for_bad_payload(self):
        self.assertEqual(sanitize_settings(None), DEFAULT_SETTINGS)

    def test_numeric_values_are_clamped(self):
        settings = sanitize_settings(
            {
                "capture": {"fps": 999},
                "source": {"cropX": 90, "cropWidth": 100},
                "headset": {"zoom": -4, "barrel": 99},
            }
        )
        self.assertEqual(settings["capture"]["fps"], 60)
        self.assertEqual(settings["source"]["cropWidth"], 10)
        self.assertEqual(settings["headset"]["zoom"], 0.6)
        self.assertEqual(settings["headset"]["barrel"], 0.5)

    def test_pause_requires_a_boolean(self):
        self.assertFalse(sanitize_settings({"capture": {"paused": 1}})["capture"]["paused"])
        self.assertTrue(sanitize_settings({"capture": {"paused": True}})["capture"]["paused"])

    def test_cursor_capture_requires_a_boolean(self):
        self.assertTrue(sanitize_settings({"capture": {"cursor": 0}})["capture"]["cursor"])
        self.assertFalse(sanitize_settings({"capture": {"cursor": False}})["capture"]["cursor"])

    def test_native_resolution_requires_a_boolean(self):
        self.assertFalse(sanitize_settings({"headset": {"nativeResolution": 1}})["headset"]["nativeResolution"])
        self.assertTrue(sanitize_settings({"headset": {"nativeResolution": True}})["headset"]["nativeResolution"])

    def test_lens_profiles_round_trip_and_replace_by_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "lens-profiles.json"
            store = LensProfileStore(path)
            saved, created = store.save(
                "VR Box Sofa",
                {"eyeWidth": 86, "barrel": 0.18, "nativeResolution": True},
            )
            self.assertTrue(created)
            self.assertEqual(saved["name"], "VR Box Sofa")
            self.assertEqual(saved["headset"]["eyeWidth"], 86)
            self.assertTrue(saved["headset"]["nativeResolution"])

            updated, created = store.save("vr box sofa", {"eyeWidth": 91})
            self.assertFalse(created)
            self.assertEqual(updated["name"], "VR Box Sofa")
            self.assertEqual(updated["headset"]["eyeWidth"], 91)
            self.assertEqual(len(store.get()), 1)

            restored = LensProfileStore(path).get()
            self.assertEqual(restored, store.get())
            self.assertEqual(store.delete("VR BOX SOFA"), "VR Box Sofa")
            self.assertEqual(store.get(), [])

    def test_display_id_accepts_windows_display_names_only(self):
        display_id = chr(92) * 2 + "." + chr(92) + "DISPLAY3"
        settings = sanitize_settings({"capture": {"display": display_id}})
        self.assertEqual(settings["capture"]["display"], display_id)
        invalid = sanitize_settings({"capture": {"display": "not-a-monitor"}})
        self.assertEqual(invalid["capture"]["display"], "all")

    def test_capture_backend_accepts_known_values_only(self):
        settings = sanitize_settings({"capture": {"backend": "dxgi"}})
        self.assertEqual(settings["capture"]["backend"], "dxgi")
        invalid = sanitize_settings({"capture": {"backend": "unknown"}})
        self.assertEqual(invalid["capture"]["backend"], "auto")

    def test_stream_frame_and_fit_mode_are_sanitized(self):
        settings = sanitize_settings(
            {
                "stream": {"width": 1279, "height": 721},
                "source": {"fit": "cover"},
            }
        )
        self.assertEqual(settings["stream"], {"width": 1278, "height": 720})
        self.assertEqual(settings["source"]["fit"], "cover")
        self.assertEqual(sanitize_settings({"source": {"fit": "invalid"}})["source"]["fit"], "contain")

    def test_stream_accepts_full_hd_maximum(self):
        settings = sanitize_settings({"stream": {"width": 1920, "height": 1080}})
        self.assertEqual(settings["stream"], {"width": 1920, "height": 1080})

    def test_stream_letterboxes_without_cropping_or_stretching(self):
        source = Image.new("RGB", (160, 90), "#ff0000")
        frame = fit_image_to_stream(source, 100, 100)
        self.assertEqual(frame.size, (100, 100))
        self.assertEqual(frame.getpixel((50, 0)), (0, 0, 0))
        self.assertEqual(frame.getpixel((50, 22)), (255, 0, 0))
        self.assertEqual(frame.getpixel((50, 78)), (0, 0, 0))

    def test_stream_uses_exact_integer_downscale_dimensions(self):
        source = Image.new("RGB", (1920, 1080), "#446688")
        frame = fit_image_to_stream(source, 960, 540)
        self.assertEqual(frame.size, (960, 540))
        self.assertEqual(frame.getpixel((480, 270)), (68, 102, 136))

    def test_stream_accepts_raw_dxgi_rgb_frames(self):
        source = np.full((1080, 1920, 3), (68, 102, 136), dtype=np.uint8)
        frame = fit_image_to_stream(source, 1280, 720)
        self.assertEqual(frame.size, (1280, 720))
        self.assertEqual(frame.getpixel((640, 360)), (68, 102, 136))

    def test_raw_dxgi_jpeg_encoder_keeps_rgb_channel_order(self):
        source = np.zeros((64, 128, 3), dtype=np.uint8)
        source[:, :64] = (255, 0, 0)
        source[:, 64:] = (0, 0, 255)
        decoded = Image.open(io.BytesIO(encode_stream_frame(source, 95))).convert("RGB")
        self.assertGreater(decoded.getpixel((20, 32))[0], 200)
        self.assertGreater(decoded.getpixel((100, 32))[2], 200)

    def test_crop_selected_monitor_uses_virtual_desktop_coordinates(self):
        image = Image.new("RGB", (200, 100), "#ff0000")
        for x in range(100, 200):
            for y in range(100):
                image.putpixel((x, y), (0, 0, 255))
        virtual = {"id": "all", "x": -100, "y": 0, "width": 200, "height": 100}
        monitor = {
            "id": chr(92) * 2 + "." + chr(92) + "DISPLAY2",
            "x": 0,
            "y": 0,
            "width": 100,
            "height": 100,
        }
        cropped = crop_image_to_display(image, virtual, monitor)
        self.assertEqual(cropped.size, (100, 100))
        self.assertEqual(cropped.getpixel((0, 0)), (0, 0, 255))

    def test_cursor_projects_into_a_letterboxed_stream(self):
        display = {"x": -100, "y": 0, "width": 200, "height": 100}
        self.assertEqual(
            project_cursor_to_stream((0, 50), display, (200, 100), (100, 100)),
            (50, 50),
        )
        self.assertIsNone(
            project_cursor_to_stream((-101, 50), display, (200, 100), (100, 100))
        )

    def test_cursor_overlay_marks_pillow_and_dxgi_frames(self):
        pillow_frame = Image.new("RGB", (100, 100), "black")
        self.assertIsNotNone(overlay_cursor_marker(pillow_frame, (20, 20)).getbbox())

        dxgi_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertTrue(np.any(overlay_cursor_marker(dxgi_frame, (20, 20))))

    def test_viewer_registry_tracks_phone_connections_safely(self):
        viewers = ViewerRegistry()
        phone_role = viewers.connect("phone")
        studio_role = viewers.connect("studio")
        self.assertEqual(viewers.snapshot(), {"studio": 1, "phone": 1, "other": 0})
        viewers.disconnect(phone_role)
        viewers.disconnect(phone_role)
        viewers.disconnect(studio_role)
        self.assertEqual(viewers.snapshot(), {"studio": 0, "phone": 0, "other": 0})

    def test_viewer_registry_keeps_latest_frame_phone_visible(self):
        viewers = ViewerRegistry()
        viewers.heartbeat("phone")
        self.assertEqual(viewers.snapshot()["phone"], 1)

    def test_latest_frame_route_returns_only_new_frames_on_one_connection(self):
        class StaticCapture:
            frame = Frame(5, b"test-jpeg", 320, 180, time.monotonic())

            def get_after(self, _sequence, timeout=4.0):
                return self.frame

        viewers = ViewerRegistry()
        server = LensCastHTTPServer(
            ("127.0.0.1", 0),
            object(),
            object(),
            StaticCapture(),
            object(),
            viewers,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
        try:
            connection.request("GET", "/frame.jpg?role=phone&after=-1")
            first = connection.getresponse()
            self.assertEqual(first.status, 200)
            self.assertEqual(first.read(), b"test-jpeg")
            self.assertEqual(first.getheader("X-LensCast-Sequence"), "5")
            self.assertEqual(viewers.snapshot()["phone"], 1)

            connection.request("GET", "/frame.jpg?role=phone&after=5")
            second = connection.getresponse()
            self.assertEqual(second.status, 204)
            self.assertEqual(second.read(), b"")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_dxgi_output_mapping_requires_one_monitor_for_virtual_desktop(self):
        display_one = {"id": "display-1"}
        display_two = {"id": "display-2"}
        displays = [{"id": "all"}, display_one, display_two]
        self.assertIsNone(dxgi_output_index(displays[0], displays))
        self.assertEqual(dxgi_output_index(display_one, displays), 0)
        self.assertEqual(dxgi_output_index(display_two, displays), 1)


if __name__ == "__main__":
    unittest.main()
