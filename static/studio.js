(function () {
  "use strict";

  var settings = null;
  var previewMode = "output";
  var saveTimer = null;
  var statusTimer = null;
  var displayTimer = null;
  var retryTimer = null;
  var streamReady = false;
  var displays = [];

  var canvas = document.getElementById("studioPreview");
  var context = canvas.getContext("2d");
  var streamImage = document.getElementById("streamImage");
  var previewShell = document.querySelector(".preview-shell");
  var controls = Array.prototype.slice.call(document.querySelectorAll("[data-path]"));
  var outputNodes = Array.prototype.slice.call(document.querySelectorAll("[data-output]"));
  var qualityPresets = {
    latency: { fps: 36, quality: 58 },
    balanced: { fps: 30, quality: 72 },
    clarity: { fps: 30, quality: 85 }
  };
  var streamPresets = {
    light: { width: 960, height: 540 },
    game: { width: 1280, height: 720 },
    sharp: { width: 1600, height: 900 }
  };
  var headsetPresets = {
    wide: {
      eyeWidth: 98, eyeHeight: 94, eyeGap: 0, eyeOffsetX: 0, eyeOffsetY: 0,
      zoom: 0.94, barrel: 0.07, curvature: 0.03, brightness: 1
    },
    balanced: {
      eyeWidth: 92, eyeHeight: 90, eyeGap: 2, eyeOffsetX: 0, eyeOffsetY: 0,
      zoom: 1, barrel: 0.12, curvature: 0.08, brightness: 1
    },
    compact: {
      eyeWidth: 83, eyeHeight: 84, eyeGap: 4, eyeOffsetX: 0, eyeOffsetY: 0,
      zoom: 1.08, barrel: 0.18, curvature: 0.13, brightness: 1
    }
  };

  function requestJson(url, options) {
    return fetch(url, options).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    });
  }

  function getPath(target, path) {
    var parts = path.split(".");
    return target[parts[0]][parts[1]];
  }

  function setPath(target, path, value) {
    var parts = path.split(".");
    target[parts[0]][parts[1]] = value;
  }

  function normaliseCrop() {
    settings.source.cropWidth = Math.min(settings.source.cropWidth, 100 - settings.source.cropX);
    settings.source.cropHeight = Math.min(settings.source.cropHeight, 100 - settings.source.cropY);
  }

  function numberFormat(value, control) {
    var decimals = Number(control.dataset.decimals || 0);
    var unit = control.dataset.unit || "";
    return Number(value).toFixed(decimals) + unit;
  }

  function rangeFill(control) {
    if (control.type !== "range") {
      return;
    }
    var min = Number(control.min);
    var max = Number(control.max);
    var value = Number(control.value);
    var fill = ((value - min) / (max - min)) * 100;
    control.style.setProperty("--fill", String(fill) + "%");
  }

  function refreshControls() {
    if (!settings) {
      return;
    }
    controls.forEach(function (control) {
      var value = getPath(settings, control.dataset.path);
      if (control.type === "checkbox") {
        control.checked = Boolean(value);
      } else {
        control.value = value;
        if (control.type === "range") {
          rangeFill(control);
        }
      }
    });
    outputNodes.forEach(function (output) {
      var control = document.querySelector("[data-path='" + output.dataset.output + "']");
      if (!control) {
        return;
      }
      output.textContent = numberFormat(getPath(settings, output.dataset.output), control);
    });
    syncPresetButtons();
    updateDisplayMeta();
    updateFitMeta();
    updateStreamMeta();
  }

  function valuesMatch(actual, expected) {
    return Object.keys(expected).every(function (key) {
      return Math.abs(Number(actual[key]) - Number(expected[key])) < 0.005;
    });
  }

  function syncPresetButtons() {
    if (!settings) {
      return;
    }
    Array.prototype.slice.call(document.querySelectorAll("[data-quality-preset]")).forEach(function (button) {
      var preset = qualityPresets[button.dataset.qualityPreset];
      button.classList.toggle("active", valuesMatch(settings.capture, preset));
    });
    Array.prototype.slice.call(document.querySelectorAll("[data-stream-preset]")).forEach(function (button) {
      var preset = streamPresets[button.dataset.streamPreset];
      button.classList.toggle("active", valuesMatch(settings.stream, preset));
    });
    Array.prototype.slice.call(document.querySelectorAll("[data-headset-preset]")).forEach(function (button) {
      var preset = headsetPresets[button.dataset.headsetPreset];
      button.classList.toggle("active", valuesMatch(settings.headset, preset));
    });
  }

  function displayForId(displayId) {
    return displays.filter(function (display) {
      return display.id === displayId;
    })[0] || null;
  }

  function updateDisplayMeta(target) {
    var node = document.getElementById("displayMeta");
    if (!node || !settings) {
      return;
    }
    var display = target || displayForId(settings.capture.display);
    if (!display) {
      node.textContent = "Monitor sebelumnya tidak ditemukan. Capture akan kembali ke desktop virtual.";
      return;
    }
    var detail = display.width > 0 && display.height > 0
      ? display.width + " x " + display.height + " px"
      : "Resolusi tersedia saat capture aktif";
    node.textContent = display.id === "all"
      ? "Mengirim seluruh area desktop virtual. " + detail + "."
      : display.label + " - " + detail + ".";
  }

  function updateBackendMeta(backend) {
    var node = document.getElementById("backendMeta");
    if (!node) {
      return;
    }
    if (!backend) {
      node.textContent = "DXGI akan dipilih otomatis untuk monitor tunggal.";
      return;
    }
    node.textContent = backend.detail || "Mesin capture sedang diperbarui.";
  }

  function updateFitMeta() {
    var node = document.getElementById("fitMeta");
    if (!node || !settings) {
      return;
    }
    if (settings.source.fit === "cover") {
      node.textContent = "Penuhi seluruh lensa; sisi gambar dapat terpotong pada game 16:9.";
    } else if (settings.source.fit === "stretch") {
      node.textContent = "Regang tanpa area hitam; rasio game dapat terlihat melebar atau gepeng.";
    } else {
      node.textContent = "Pas menjaga game tetap utuh; area lensa kosong dibuat hitam otomatis.";
    }
  }

  function updateStreamMeta() {
    var label = document.getElementById("streamResolution");
    var note = document.getElementById("streamMeta");
    if (!settings || !settings.stream) {
      return;
    }
    var width = settings.stream.width;
    var height = settings.stream.height;
    var pixels = width * height / 1000000;
    if (label) {
      label.textContent = width + " x " + height;
    }
    if (note) {
      note.textContent = "" + width + " x " + height + " (" + pixels.toFixed(2) + " MP). Satu frame 16:9 digandakan oleh GPU HP.";
    }
  }

  function applySettingsGroup(group, values) {
    Object.keys(values).forEach(function (key) {
      settings[group][key] = values[key];
    });
    normaliseCrop();
    refreshControls();
    queueSave();
  }

  function queueSave() {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(function () {
      requestJson("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings)
      }).then(function (serverSettings) {
        settings = serverSettings;
        refreshControls();
      })["catch"](function () {
        setConnectionText("Kontrol tersimpan lokal, server belum merespons");
      });
    }, 180);
  }

  function updateControl(control) {
    var value;
    if (control.type === "checkbox") {
      value = control.checked;
    } else if (control.tagName === "SELECT") {
      value = control.value;
    } else {
      value = Number(control.value);
    }
    setPath(settings, control.dataset.path, value);
    normaliseCrop();
    refreshControls();
    queueSave();
  }

  controls.forEach(function (control) {
    var eventName = control.type === "checkbox" || control.tagName === "SELECT" ? "change" : "input";
    control.addEventListener(eventName, function () {
      updateControl(control);
    });
  });

  function setConnectionText(text) {
    var node = document.getElementById("connectionStatus");
    if (node) {
      node.textContent = text;
    }
  }

  function updateStatus() {
    return requestJson("/api/status").then(function (status) {
      var capture = status.capture;
      var viewers = status.viewers || { studio: 0, phone: 0, other: 0 };
      var target = capture.target || {};
      var backend = capture.backend || {};
      var phoneCount = Number(viewers.phone || 0);
      var captureReadout = document.getElementById("captureReadout");
      var state = document.getElementById("streamState");
      captureReadout.textContent = capture.width + " x " + capture.height + " - " + capture.measuredFps + " fps";
      document.getElementById("captureTargetReadout").textContent = target.label || "DESKTOP VIRTUAL";
      document.getElementById("streamReadout").textContent = capture.bitrateMbps + " Mbps / " + capture.ageMs + " ms";
      document.getElementById("backendReadout").textContent =
        String(backend.active || "menunggu").toUpperCase() + " / " + String(backend.requested || "auto").toUpperCase();
      document.getElementById("headsetReadout").textContent = phoneCount + " HP TERHUBUNG";
      updateDisplayMeta(displayForId(target.id));
      updateBackendMeta(backend);

      if (capture.error) {
        state.textContent = "CAPTURE PERLU PERHATIAN";
        setConnectionText("Capture error: " + capture.error);
      } else if (settings && settings.capture.paused) {
        state.textContent = "CAPTURE DIJEDA";
        setConnectionText("Stream dijeda dari Studio");
      } else if (target.available === false) {
        state.textContent = "MONITOR BERUBAH";
        setConnectionText("Monitor dipilih tidak tersedia; capture kembali ke desktop virtual");
      } else if (capture.measuredFps > 0 && capture.measuredFps < 30) {
        state.textContent = "FPS DI BAWAH TARGET";
        setConnectionText("Pilih profil Game 30+ atau turunkan resolusi capture.");
      } else {
        state.textContent = "DESKTOP LIVE";
        setConnectionText(phoneCount > 0
          ? phoneCount + " HP sedang menerima stream"
          : "Server lokal siap menerima HP");
      }

      var phoneUrl = status.phoneUrls && status.phoneUrls[0];
      if (phoneUrl) {
        document.getElementById("phoneUrl").textContent = phoneUrl;
      } else {
        document.getElementById("phoneUrl").textContent = "IP LAN tidak ditemukan - cek Wi-Fi PC";
      }
    })["catch"](function () {
      document.getElementById("streamState").textContent = "SERVER TIDAK TERHUBUNG";
      setConnectionText("Tidak bisa membaca status server");
    });
  }

  function loadDisplays() {
    return requestJson("/api/displays").then(function (payload) {
      displays = payload.displays || [];
      var select = document.getElementById("displaySelect");
      var selectedId = settings ? settings.capture.display : "all";
      select.textContent = "";
      displays.forEach(function (display) {
        var option = document.createElement("option");
        option.value = display.id;
        option.textContent = display.label;
        select.appendChild(option);
      });
      if (!displayForId(selectedId)) {
        var unavailable = document.createElement("option");
        unavailable.value = selectedId;
        unavailable.textContent = "Monitor sebelumnya tidak tersedia";
        select.appendChild(unavailable);
      }
      select.value = selectedId;
      updateDisplayMeta();
    })["catch"](function () {
      var node = document.getElementById("displayMeta");
      node.textContent = "Daftar monitor belum dapat dibaca dari Windows.";
    });
  }

  document.getElementById("copyUrlButton").addEventListener("click", function () {
    var url = document.getElementById("phoneUrl").textContent;
    if (!url || url.indexOf("http") !== 0) {
      return;
    }
    copyText(url).then(function () {
      document.getElementById("copyUrlButton").textContent = "Tersalin";
      window.setTimeout(function () {
        document.getElementById("copyUrlButton").textContent = "Salin";
      }, 1300);
    });
  });

  function copyText(value) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve) {
      var helper = document.createElement("textarea");
      helper.value = value;
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      helper.remove();
      resolve();
    });
  }

  document.getElementById("resetButton").addEventListener("click", function () {
    if (!window.confirm("Kembalikan semua kalibrasi ke nilai awal?")) {
      return;
    }
    requestJson("/api/reset", { method: "POST" }).then(function (newSettings) {
      settings = newSettings;
      refreshControls();
    });
  });

  Array.prototype.slice.call(document.querySelectorAll("[data-preset='full']")).forEach(function (button) {
    button.addEventListener("click", function () {
      settings.source.cropX = 0;
      settings.source.cropY = 0;
      settings.source.cropWidth = 100;
      settings.source.cropHeight = 100;
      refreshControls();
      queueSave();
    });
  });

  document.getElementById("refreshDisplaysButton").addEventListener("click", function () {
    loadDisplays();
  });

  Array.prototype.slice.call(document.querySelectorAll("[data-quality-preset]")).forEach(function (button) {
    button.addEventListener("click", function () {
      applySettingsGroup("capture", qualityPresets[button.dataset.qualityPreset]);
    });
  });

  Array.prototype.slice.call(document.querySelectorAll("[data-stream-preset]")).forEach(function (button) {
    button.addEventListener("click", function () {
      applySettingsGroup("stream", streamPresets[button.dataset.streamPreset]);
    });
  });

  Array.prototype.slice.call(document.querySelectorAll("[data-headset-preset]")).forEach(function (button) {
    button.addEventListener("click", function () {
      applySettingsGroup("headset", headsetPresets[button.dataset.headsetPreset]);
    });
  });

  document.getElementById("fullscreenPreviewButton").addEventListener("click", function () {
    if (document.fullscreenElement === previewShell) {
      document.exitFullscreen();
    } else if (previewShell.requestFullscreen) {
      previewShell.requestFullscreen()["catch"](function () {});
    }
  });

  document.addEventListener("fullscreenchange", function () {
    document.getElementById("fullscreenPreviewButton").textContent =
      document.fullscreenElement === previewShell ? "Keluar fullscreen" : "Layar penuh";
  });

  Array.prototype.slice.call(document.querySelectorAll("[data-preview-mode]")).forEach(function (button) {
    button.addEventListener("click", function () {
      previewMode = button.dataset.previewMode;
      Array.prototype.slice.call(document.querySelectorAll("[data-preview-mode]")).forEach(function (node) {
        node.classList.toggle("active", node === button);
      });
      document.getElementById("renderReadout").textContent = previewMode === "output"
        ? "DUAL EYE / LENS WARP"
        : "RAW DESKTOP / CROP GUIDE";
    });
  });

  function drawBackdrop() {
    var width = canvas.width;
    var height = canvas.height;
    var gradient = context.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, "#0d1717");
    gradient.addColorStop(1, "#060a0a");
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);
    context.strokeStyle = "rgba(209, 239, 203, 0.055)";
    context.lineWidth = 1;
    for (var x = 0; x < width; x += 80) {
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, height);
      context.stroke();
    }
    for (var y = 0; y < height; y += 80) {
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(width, y);
      context.stroke();
    }
  }

  function cropRect(image) {
    var source = settings.source;
    return {
      x: Math.round(image.naturalWidth * source.cropX / 100),
      y: Math.round(image.naturalHeight * source.cropY / 100),
      width: Math.max(1, Math.round(image.naturalWidth * source.cropWidth / 100)),
      height: Math.max(1, Math.round(image.naturalHeight * source.cropHeight / 100))
    };
  }

  function drawImageFit(image, crop, x, y, width, height, fit, zoom, shiftX, shiftY) {
    var renderedWidth;
    var renderedHeight;
    if (fit === "stretch") {
      renderedWidth = width * zoom;
      renderedHeight = height * zoom;
    } else {
      var scaleBase = fit === "cover" ? Math.max : Math.min;
      var ratio = scaleBase(width / crop.width, height / crop.height) * zoom;
      renderedWidth = crop.width * ratio;
      renderedHeight = crop.height * ratio;
    }
    context.save();
    context.beginPath();
    context.rect(x, y, width, height);
    context.clip();
    context.drawImage(
      image,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      x + (width - renderedWidth) / 2 + shiftX,
      y + (height - renderedHeight) / 2 + shiftY,
      renderedWidth,
      renderedHeight
    );
    context.restore();
  }

  function drawSourcePreview(image) {
    var margin = 62;
    var availableWidth = canvas.width - margin * 2;
    var availableHeight = canvas.height - margin * 2;
    var scale = Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight);
    var drawWidth = image.naturalWidth * scale;
    var drawHeight = image.naturalHeight * scale;
    var drawX = (canvas.width - drawWidth) / 2;
    var drawY = (canvas.height - drawHeight) / 2;
    var crop = cropRect(image);

    context.drawImage(image, drawX, drawY, drawWidth, drawHeight);
    context.fillStyle = "rgba(0, 0, 0, 0.5)";
    context.fillRect(drawX, drawY, drawWidth, drawHeight);
    context.drawImage(
      image,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      drawX + crop.x * scale,
      drawY + crop.y * scale,
      crop.width * scale,
      crop.height * scale
    );

    var frameX = drawX + crop.x * scale;
    var frameY = drawY + crop.y * scale;
    var frameWidth = crop.width * scale;
    var frameHeight = crop.height * scale;
    context.strokeStyle = "#c8ff70";
    context.lineWidth = 3;
    context.setLineDash([11, 7]);
    context.strokeRect(frameX, frameY, frameWidth, frameHeight);
    context.setLineDash([]);
    context.fillStyle = "#c8ff70";
    context.font = "600 15px Cascadia Mono, monospace";
    context.fillText("AREA SUMBER", frameX + 9, frameY - 11);
  }

  function drawCurveGuide(x, y, width, height, curvature, color) {
    var amount = curvature * height * 0.22;
    context.strokeStyle = color;
    context.lineWidth = 1.5;
    context.globalAlpha = 0.75;
    context.beginPath();
    context.moveTo(x + width * 0.1, y + height * 0.08);
    context.quadraticCurveTo(x + width * 0.5, y + height * 0.08 + amount, x + width * 0.9, y + height * 0.08);
    context.stroke();
    context.beginPath();
    context.moveTo(x + width * 0.1, y + height * 0.92);
    context.quadraticCurveTo(x + width * 0.5, y + height * 0.92 - amount, x + width * 0.9, y + height * 0.92);
    context.stroke();
    context.globalAlpha = 1;
  }

  function drawEye(image, crop, x, y, width, height, side, imageShiftX, imageShiftY) {
    var color = side === "L" ? "#ff775f" : "#75e2da";
    drawImageFit(
      image,
      crop,
      x,
      y,
      width,
      height,
      settings.source.fit,
      settings.headset.zoom,
      imageShiftX,
      imageShiftY
    );
    context.fillStyle = "rgba(0, 0, 0, 0.14)";
    context.fillRect(x, y, width, height);

    context.strokeStyle = color;
    context.lineWidth = 2;
    context.strokeRect(x, y, width, height);
    context.strokeStyle = "rgba(240, 255, 236, 0.17)";
    context.lineWidth = 1;
    context.setLineDash([5, 6]);
    context.beginPath();
    context.moveTo(x + width / 2, y);
    context.lineTo(x + width / 2, y + height);
    context.moveTo(x, y + height / 2);
    context.lineTo(x + width, y + height / 2);
    context.stroke();
    context.setLineDash([]);
    drawCurveGuide(x, y, width, height, settings.headset.curvature, color);

    context.fillStyle = color;
    context.font = "700 14px Cascadia Mono, monospace";
    context.fillText(side + " EYE", x + 10, y + 21);
  }

  function drawOutputPreview(image) {
    var headset = settings.headset;
    var outerMarginX = 72;
    var outerMarginY = 82;
    var availableWidth = canvas.width - outerMarginX * 2;
    var availableHeight = canvas.height - outerMarginY * 2;
    var eyeCellWidth = availableWidth / 2;
    var eyeWidth = eyeCellWidth * headset.eyeWidth / 100;
    var eyeHeight = availableHeight * headset.eyeHeight / 100;
    var gap = eyeCellWidth * headset.eyeGap / 100;
    var verticalShift = availableHeight * headset.eyeOffsetY / 100;
    var horizontalShift = eyeWidth * headset.eyeOffsetX / 100;
    var leftX = outerMarginX + (eyeCellWidth - eyeWidth) / 2 - gap / 2;
    var rightX = outerMarginX + eyeCellWidth + (eyeCellWidth - eyeWidth) / 2 + gap / 2;
    var eyeY = outerMarginY + (availableHeight - eyeHeight) / 2;
    var crop = cropRect(image);

    context.fillStyle = "#000";
    context.fillRect(outerMarginX, outerMarginY, availableWidth, availableHeight);
    context.strokeStyle = "rgba(208, 232, 205, 0.23)";
    context.lineWidth = 1;
    context.strokeRect(outerMarginX, outerMarginY, availableWidth, availableHeight);
    context.fillStyle = "rgba(200, 255, 112, 0.1)";
    context.fillRect(canvas.width / 2 - 1, outerMarginY, 2, availableHeight);

    drawEye(image, crop, leftX, eyeY, eyeWidth, eyeHeight, "L", -horizontalShift, verticalShift);
    drawEye(image, crop, rightX, eyeY, eyeWidth, eyeHeight, "R", horizontalShift, verticalShift);

    context.strokeStyle = "rgba(200, 255, 112, 0.76)";
    context.lineWidth = 1;
    context.setLineDash([8, 8]);
    context.strokeRect(outerMarginX, outerMarginY, availableWidth, availableHeight);
    context.setLineDash([]);
    context.fillStyle = "rgba(200, 255, 112, 0.86)";
    context.font = "600 12px Cascadia Mono, monospace";
    context.fillText("PHONE SAFE AREA", outerMarginX + 8, outerMarginY - 12);
  }

  function drawEmptyState() {
    context.fillStyle = "#c8ff70";
    context.font = "700 19px Bahnschrift, sans-serif";
    context.fillText("MENUNGGU DESKTOP CAPTURE", 65, canvas.height / 2 - 12);
    context.fillStyle = "#9cab9f";
    context.font = "14px Aptos, sans-serif";
    context.fillText("Izinkan screen capture Windows jika diminta.", 65, canvas.height / 2 + 19);
  }

  function render() {
    drawBackdrop();
    if (settings && streamImage.naturalWidth > 0 && streamImage.naturalHeight > 0) {
      if (previewMode === "source") {
        drawSourcePreview(streamImage);
      } else {
        drawOutputPreview(streamImage);
      }
    } else {
      drawEmptyState();
    }
    window.requestAnimationFrame(render);
  }

  function startStream() {
    streamImage.onload = function () {
      streamReady = true;
      window.clearTimeout(retryTimer);
    };
    streamImage.onerror = function () {
      streamReady = false;
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(startStream, 1200);
    };
    streamImage.src = "/stream.mjpg?role=studio&start=" + Date.now();
  }

  function boot() {
    requestJson("/api/settings").then(function (initialSettings) {
      settings = initialSettings;
      refreshControls();
      return Promise.all([updateStatus(), loadDisplays()]);
    })["catch"](function (error) {
      document.getElementById("streamState").textContent = "GAGAL MEMUAT STUDIO";
      setConnectionText(error.message);
    });
    statusTimer = window.setInterval(updateStatus, 2500);
    displayTimer = window.setInterval(loadDisplays, 10000);
    startStream();
    render();
  }

  window.addEventListener("beforeunload", function () {
    window.clearInterval(statusTimer);
    window.clearInterval(displayTimer);
    window.clearTimeout(saveTimer);
    window.clearTimeout(retryTimer);
  });

  boot();
}());
