import unittest

from PIL import Image

from app import (
    DEFAULT_SETTINGS,
    ViewerRegistry,
    crop_image_to_display,
    dxgi_output_index,
    sanitize_settings,
)


class SettingsValidationTests(unittest.TestCase):
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

    def test_viewer_registry_tracks_phone_connections_safely(self):
        viewers = ViewerRegistry()
        phone_role = viewers.connect("phone")
        studio_role = viewers.connect("studio")
        self.assertEqual(viewers.snapshot(), {"studio": 1, "phone": 1, "other": 0})
        viewers.disconnect(phone_role)
        viewers.disconnect(phone_role)
        viewers.disconnect(studio_role)
        self.assertEqual(viewers.snapshot(), {"studio": 0, "phone": 0, "other": 0})

    def test_dxgi_output_mapping_requires_one_monitor_for_virtual_desktop(self):
        display_one = {"id": "display-1"}
        display_two = {"id": "display-2"}
        displays = [{"id": "all"}, display_one, display_two]
        self.assertIsNone(dxgi_output_index(displays[0], displays))
        self.assertEqual(dxgi_output_index(display_one, displays), 0)
        self.assertEqual(dxgi_output_index(display_two, displays), 1)


if __name__ == "__main__":
    unittest.main()
