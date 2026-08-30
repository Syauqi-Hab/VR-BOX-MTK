#!/usr/bin/env python3
"""Native one-click PC connection dashboard for LensCast VR."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from app import (
    adb_connected_serials,
    find_adb_path,
    hidden_subprocess_kwargs,
    main as run_lenscast_server,
)


RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_DIR
SERVER_URL = "http://127.0.0.1:8264"
STUDIO_URL = f"{SERVER_URL}/studio"
PHONE_PACKAGE = "com.syauqihab.lenscastvr"
PHONE_ACTIVITY = f"{PHONE_PACKAGE}/.MainActivity"

INK = "#091011"
PANEL = "#142222"
PANEL_SOFT = "#1b2e2d"
LINE = "#36504b"
PAPER = "#edf3e8"
MUTED = "#9aaca3"
LIME = "#c8ff70"
LIME_DARK = "#8bb94b"
CORAL = "#ff775f"
CYAN = "#75e2da"
DISPLAY_FONT = "Bahnschrift"
BODY_FONT = "Segoe UI"
MONO_FONT = "Cascadia Mono"


def read_status() -> dict[str, Any] | None:
    """Read local server telemetry without ever contacting the LAN."""
    try:
        with urlopen(f"{SERVER_URL}/api/status", timeout=0.35) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return None


class LensCastController:
    """Owns the local server process and the short USB connection handshake."""

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def connect_usb(self) -> str:
        server_message = self._start_server()
        if read_status() is None:
            return server_message

        adb_path = find_adb_path()
        if not adb_path:
            return "Server lokal siap. Android Platform Tools belum ditemukan."

        try:
            self._run_adb(adb_path, "start-server")
            devices = self._run_adb(adb_path, "devices")
        except (OSError, subprocess.SubprocessError):
            return "Server siap, tetapi ADB tidak dapat dijalankan."

        serials = adb_connected_serials(devices.stdout)
        if not serials:
            return "Server USB siap. Hubungkan HP, aktifkan USB debugging, lalu tekan Connect USB lagi."

        connected = 0
        for serial in serials:
            tunnel = self._run_adb(adb_path, "-s", serial, "reverse", "tcp:8264", "tcp:8264")
            if tunnel.returncode != 0:
                continue
            self._run_adb(adb_path, "-s", serial, "shell", "am", "start", "-n", PHONE_ACTIVITY)
            connected += 1

        if connected:
            return "USB siap. LensCast VR dibuka di HP; tunggu stream muncul."
        return "HP terdeteksi, tetapi tunnel USB gagal dibuat. Terima izin USB debugging lalu coba lagi."

    def stop(self) -> str:
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return "Tidak ada server yang dijalankan oleh LensCast PC."
        process.terminate()
        try:
            process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            process.kill()
        return "LensCast USB dihentikan."

    def _start_server(self) -> str:
        if read_status() is not None:
            return "Server lokal sudah aktif."
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                environment = os.environ.copy()
                environment["LENSCAST_CONFIG_PATH"] = str(DATA_DIR / "lenscast-settings.json")
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self._process = subprocess.Popen(
                    self._server_command(),
                    cwd=RESOURCE_DIR,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if read_status() is not None:
                return "Server USB lokal siap."
            with self._lock:
                process = self._process
            if process is not None and process.poll() is not None:
                return "Server tidak dapat dimulai. Periksa Python dan dependensi LensCast."
            time.sleep(0.2)
        return "Server sedang memulai. Tunggu sebentar lalu tekan Connect USB lagi."

    def _server_command(self) -> list[str]:
        server_arguments = ["--server", "--host", "127.0.0.1", "--adb-reverse", "--no-browser"]
        if getattr(sys, "frozen", False):
            return [sys.executable, *server_arguments]
        return [sys.executable, str(Path(__file__).resolve()), *server_arguments]

    @staticmethod
    def _run_adb(adb_path: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [adb_path, *arguments],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **hidden_subprocess_kwargs(),
        )


class LensCastPCApp:
    """Tkinter shell that keeps startup simple while Studio handles calibration."""

    def __init__(self) -> None:
        self.controller = LensCastController()
        self.root = tk.Tk()
        self.root.title("LensCast PC")
        self.root.geometry("970x650")
        self.root.minsize(820, 580)
        self.root.configure(bg=INK)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.state_var = tk.StringVar(value="SIAP UNTUK USB")
        self.message_var = tk.StringVar(value="Hubungkan HP via kabel USB-C lalu tekan Connect USB.")
        self.server_var = tk.StringVar(value="SERVER  OFFLINE")
        self.phone_var = tk.StringVar(value="HP  BELUM TERHUBUNG")
        self.capture_var = tk.StringVar(value="CAPTURE  MENUNGGU")
        self.tunnel_var = tk.StringVar(value="TUNNEL  AKAN OTOMATIS DIPULIHKAN")
        self._busy = False
        self._build_interface()
        self._refresh_status()

    def run(self) -> None:
        self.root.mainloop()

    def _build_interface(self) -> None:
        header = tk.Frame(self.root, bg=INK, height=84)
        header.pack(fill="x", padx=30, pady=(22, 8))
        header.pack_propagate(False)

        mark = tk.Canvas(header, width=42, height=42, bg=INK, highlightthickness=0)
        mark.create_rectangle(4, 4, 19, 19, fill=LIME, outline="")
        mark.create_rectangle(23, 23, 38, 38, fill=CORAL, outline="")
        mark.create_rectangle(1, 1, 41, 41, outline=LIME_DARK, width=1)
        mark.pack(side="left", padx=(0, 13))

        title_box = tk.Frame(header, bg=INK)
        title_box.pack(side="left", fill="y")
        tk.Label(
            title_box,
            text="LENSCAST  PC",
            bg=INK,
            fg=PAPER,
            font=(DISPLAY_FONT, 19, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="VR STREAM CONTROL CENTER",
            bg=INK,
            fg=LIME,
            font=(MONO_FONT, 9, "bold"),
        ).pack(anchor="w", pady=(2, 0))

        state_box = tk.Frame(header, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1)
        state_box.pack(side="right", pady=12)
        tk.Label(state_box, text="  ", bg=LIME, width=1).pack(side="left", padx=(10, 7), pady=8)
        tk.Label(
            state_box,
            textvariable=self.state_var,
            bg=PANEL_SOFT,
            fg=PAPER,
            font=(MONO_FONT, 9, "bold"),
        ).pack(side="left", padx=(0, 11), pady=8)

        content = tk.Frame(self.root, bg=INK)
        content.pack(fill="both", expand=True, padx=30, pady=(10, 30))
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        usb_card = tk.Frame(content, bg=PANEL, highlightbackground=LIME_DARK, highlightthickness=1)
        usb_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        usb_inner = tk.Frame(usb_card, bg=PANEL)
        usb_inner.pack(fill="both", expand=True, padx=30, pady=28)

        tk.Label(usb_inner, text="REKOMENDASI", bg=PANEL, fg=LIME, font=(MONO_FONT, 10, "bold")).pack(anchor="w")
        tk.Label(
            usb_inner,
            text="Koneksi kabel\nsekali tekan.",
            bg=PANEL,
            fg=PAPER,
            justify="left",
            font=(DISPLAY_FONT, 28, "bold"),
        ).pack(anchor="w", pady=(10, 8))
        tk.Label(
            usb_inner,
            text="Menyalakan server lokal, memperbaiki ADB tunnel, dan membuka LensCast VR di HP otomatis.",
            bg=PANEL,
            fg=MUTED,
            justify="left",
            wraplength=450,
            font=(BODY_FONT, 11),
        ).pack(anchor="w")

        flow = tk.Frame(usb_inner, bg=PANEL)
        flow.pack(fill="x", pady=(28, 20))
        for index, label in enumerate(("PC", "USB-C", "HP", "VR BOX")):
            chip = tk.Label(
                flow,
                text=label,
                bg=PANEL_SOFT if index != 1 else "#23423c",
                fg=CYAN if index != 1 else LIME,
                font=(MONO_FONT, 9, "bold"),
                padx=12,
                pady=8,
            )
            chip.pack(side="left")
            if index < 3:
                tk.Label(flow, text="  >  ", bg=PANEL, fg=MUTED, font=(MONO_FONT, 10)).pack(side="left")

        self.connect_button = tk.Button(
            usb_inner,
            text="CONNECT USB",
            command=self._connect_usb,
            bg=LIME,
            fg=INK,
            activebackground="#e0ffa6",
            activeforeground=INK,
            bd=0,
            padx=24,
            pady=14,
            font=(DISPLAY_FONT, 13, "bold"),
            cursor="hand2",
        )
        self.connect_button.pack(anchor="w")

        actions = tk.Frame(usb_inner, bg=PANEL)
        actions.pack(fill="x", pady=(12, 0))
        tk.Button(
            actions,
            text="BUKA STUDIO KALIBRASI",
            command=self._open_studio,
            bg=PANEL_SOFT,
            fg=PAPER,
            activebackground="#29423e",
            activeforeground=PAPER,
            bd=0,
            padx=13,
            pady=10,
            font=(MONO_FONT, 9, "bold"),
            cursor="hand2",
        ).pack(side="left")
        tk.Button(
            actions,
            text="STOP",
            command=self._stop,
            bg=PANEL,
            fg=CORAL,
            activebackground=PANEL,
            activeforeground="#ffad9c",
            bd=0,
            padx=13,
            pady=10,
            font=(MONO_FONT, 9, "bold"),
            cursor="hand2",
        ).pack(side="left", padx=(7, 0))

        side = tk.Frame(content, bg=INK)
        side.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        side.rowconfigure(1, weight=1)

        wifi_card = tk.Frame(side, bg="#172121", highlightbackground=LINE, highlightthickness=1)
        wifi_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(wifi_card, text="ALTERNATIF", bg="#172121", fg=MUTED, font=(MONO_FONT, 9, "bold")).pack(anchor="w", padx=20, pady=(19, 5))
        tk.Label(wifi_card, text="Wi-Fi lokal", bg="#172121", fg=PAPER, font=(DISPLAY_FONT, 18, "bold")).pack(anchor="w", padx=20)
        tk.Label(
            wifi_card,
            text="Terkunci pada build ini agar desktop tidak terbuka ke jaringan tanpa persetujuan.",
            bg="#172121",
            fg=MUTED,
            justify="left",
            wraplength=290,
            font=(BODY_FONT, 10),
        ).pack(anchor="w", padx=20, pady=(7, 13))
        tk.Button(
            wifi_card,
            text="WI-FI TERKUNCI",
            state="disabled",
            bg="#263532",
            disabledforeground="#7b8983",
            bd=0,
            padx=13,
            pady=9,
            font=(MONO_FONT, 9, "bold"),
        ).pack(anchor="w", padx=20, pady=(0, 19))

        telemetry = tk.Frame(side, bg=PANEL_SOFT, highlightbackground=LINE, highlightthickness=1)
        telemetry.grid(row=1, column=0, sticky="nsew")
        tk.Label(telemetry, text="TELEMETRY LANGSUNG", bg=PANEL_SOFT, fg=LIME, font=(MONO_FONT, 9, "bold")).pack(anchor="w", padx=20, pady=(20, 13))
        for variable in (self.server_var, self.phone_var, self.capture_var, self.tunnel_var):
            tk.Label(
                telemetry,
                textvariable=variable,
                bg=PANEL_SOFT,
                fg=PAPER,
                anchor="w",
                justify="left",
                font=(MONO_FONT, 9),
            ).pack(fill="x", padx=20, pady=7)

        footer = tk.Frame(self.root, bg="#0d1717", highlightbackground=LINE, highlightthickness=1)
        footer.pack(fill="x", padx=30, pady=(0, 22))
        tk.Label(
            footer,
            textvariable=self.message_var,
            bg="#0d1717",
            fg=MUTED,
            anchor="w",
            justify="left",
            padx=15,
            pady=12,
            font=(BODY_FONT, 10),
        ).pack(fill="x")

    def _connect_usb(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.connect_button.configure(state="disabled", text="MENYAMBUNG...")
        self.state_var.set("MENYIAPKAN USB")
        self.message_var.set("Menyalakan LensCast lokal dan memeriksa ADB...")

        def connect() -> None:
            message = self.controller.connect_usb()
            self.root.after(0, lambda: self._finish_connect(message))

        threading.Thread(target=connect, name="lenscast-usb-connect", daemon=True).start()

    def _finish_connect(self, message: str) -> None:
        self._busy = False
        self.connect_button.configure(state="normal", text="CONNECT USB")
        self.message_var.set(message)
        self._refresh_status()

    def _open_studio(self) -> None:
        if read_status() is None:
            self.message_var.set("Tekan Connect USB terlebih dahulu untuk menjalankan server lokal.")
            return
        webbrowser.open(STUDIO_URL)
        self.message_var.set("Studio kalibrasi dibuka di browser PC.")

    def _stop(self) -> None:
        self.message_var.set(self.controller.stop())
        self.state_var.set("SERVER DIHENTIKAN")
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = read_status()
        if status is None:
            self.server_var.set("SERVER  OFFLINE")
            self.phone_var.set("HP  BELUM TERHUBUNG")
            self.capture_var.set("CAPTURE  MENUNGGU")
            if not self._busy:
                self.state_var.set("SIAP UNTUK USB")
        else:
            capture = status.get("capture", {})
            viewers = status.get("viewers", {})
            phone_count = int(viewers.get("phone", 0))
            self.server_var.set("SERVER  LOKAL AKTIF")
            self.phone_var.set(f"HP  {phone_count} TERHUBUNG" if phone_count else "HP  MENUNGGU LENSCAST VR")
            self.capture_var.set(
                "CAPTURE  " + str(capture.get("measuredFps", 0)) + " FPS / " +
                str(capture.get("width", "--")) + "x" + str(capture.get("height", "--"))
            )
            self.tunnel_var.set("TUNNEL  ADB AUTO-RECOVER AKTIF")
            if not self._busy:
                self.state_var.set("STREAM LIVE" if phone_count else "MENUNGGU HP")
        self.root.after(1000, self._refresh_status)

    def _close(self) -> None:
        self.controller.stop()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--server", action="store_true")
    launcher_args, server_args = parser.parse_known_args()
    if launcher_args.server:
        sys.argv = [sys.argv[0], *server_args]
        return run_lenscast_server()

    LensCastPCApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
