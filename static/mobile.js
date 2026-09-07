(function () {
  "use strict";

  var settings = null;
  var renderer = null;
  var saveTimer = null;
  var retryTimer = null;
  var pollingTimer = null;
  var statusTimer = null;
  var frameRequestController = null;
  var frameRequestRunning = false;
  var frameTransportStopped = false;
  var frameSequence = -1;
  var currentFrame = null;
  var currentFrameRevision = 0;
  var latestFrameAgeMs = null;
  var usingMjpegFallback = false;
  var lastLocalChange = 0;
  var wakeLock = null;
  var renderDirty = true;
  var nextKeepAliveAt = 0;
  var deliveredFrameTimes = [];
  var lastDeliveredFrameAt = 0;
  var lastFpsCounterUpdateAt = 0;

  var canvas = document.getElementById("vrCanvas");
  var streamImage = document.getElementById("phoneStream");
  var fpsCounter = document.getElementById("vrFpsCounter");
  var onboarding = document.getElementById("onboarding");
  var chrome = document.getElementById("phoneChrome");
  var tuneSheet = document.getElementById("tuneSheet");
  var stateLabel = document.getElementById("mobileState");
  var orientationNotice = document.getElementById("orientationNotice");
  var controls = Array.prototype.slice.call(document.querySelectorAll("[data-path]"));
  var outputNodes = Array.prototype.slice.call(document.querySelectorAll("[data-output]"));

  var vertexShaderSource = [
    "attribute vec2 aPosition;",
    "varying vec2 vUv;",
    "void main() {",
    "  vUv = aPosition * 0.5 + 0.5;",
    "  gl_Position = vec4(aPosition, 0.0, 1.0);",
    "}"
  ].join("\n");

  var fragmentShaderSource = [
    "precision highp float;",
    "uniform sampler2D uTexture;",
    "uniform vec4 uCrop;",
    "uniform vec2 uFrame;",
    "uniform float uSourceAspect;",
    "uniform float uEyeAspect;",
    "uniform float uFitMode;",
    "uniform float uGap;",
    "uniform vec2 uOffset;",
    "uniform float uZoom;",
    "uniform float uBarrel;",
    "uniform float uCurvature;",
    "uniform float uBrightness;",
    "uniform float uFlipVertical;",
    "varying vec2 vUv;",
    "void main() {",
    "  float eyeIndex = floor(vUv.x * 2.0);",
    "  float eyeSign = eyeIndex < 0.5 ? -1.0 : 1.0;",
    "  vec2 eyeUv = vec2(fract(vUv.x * 2.0), vUv.y);",
    "  vec2 center = vec2(0.5 + eyeSign * uGap * 0.5, 0.5);",
    "  vec2 point = (eyeUv - center) / uFrame;",
    "  if (abs(point.x) > 0.5 || abs(point.y) > 0.5) {",
    "    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);",
    "    return;",
    "  }",
    "  point.x -= eyeSign * uOffset.x;",
    "  point.y -= uOffset.y;",
    "  float radiusSquared = dot(point, point);",
    "  point *= 1.0 + uBarrel * radiusSquared;",
    "  point.x *= 1.0 - uCurvature * 4.0 * point.x * point.x;",
    "  if (abs(point.x) > 0.5 || abs(point.y) > 0.5) {",
    "    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);",
    "    return;",
    "  }",
    "  vec2 displaySize = vec2(1.0);",
    "  if (uFitMode < 0.5) {",
    "    if (uSourceAspect > uEyeAspect) {",
    "      displaySize.y = uEyeAspect / uSourceAspect;",
    "    } else {",
    "      displaySize.x = uSourceAspect / uEyeAspect;",
    "    }",
    "  } else if (uFitMode < 1.5) {",
    "    if (uSourceAspect > uEyeAspect) {",
    "      displaySize.x = uSourceAspect / uEyeAspect;",
    "    } else {",
    "      displaySize.y = uEyeAspect / uSourceAspect;",
    "    }",
    "  }",
    "  displaySize *= uZoom;",
    "  vec2 sourcePoint = (point + 0.5 - (vec2(1.0) - displaySize) * 0.5) / displaySize;",
    "  if (sourcePoint.x < 0.0 || sourcePoint.x > 1.0 || sourcePoint.y < 0.0 || sourcePoint.y > 1.0) {",
    "    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);",
    "    return;",
    "  }",
    "  if (uFlipVertical > 0.5) {",
    "    sourcePoint.y = 1.0 - sourcePoint.y;",
    "  }",
    "  vec2 sourceUv = uCrop.xy + sourcePoint * uCrop.zw;",
    "  vec3 color = texture2D(uTexture, sourceUv).rgb * uBrightness;",
    "  gl_FragColor = vec4(color, 1.0);",
    "}"
  ].join("\n");

  function getPath(target, path) {
    var parts = path.split(".");
    return target[parts[0]][parts[1]];
  }

  function setPath(target, path, value) {
    var parts = path.split(".");
    target[parts[0]][parts[1]] = value;
  }

  function requestJson(url, options) {
    return fetch(url, options).then(function (response) {
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      return response.json();
    });
  }

  function nowMilliseconds() {
    return window.performance && window.performance.now ? window.performance.now() : Date.now();
  }

  function frameSourceWidth(source) {
    return Number(source && (source.naturalWidth || source.width) || 0);
  }

  function frameSourceHeight(source) {
    return Number(source && (source.naturalHeight || source.height) || 0);
  }

  function closeFrameSource(source) {
    if (source && typeof source.close === "function") {
      try {
        source.close();
      } catch (error) {
        // A source can already be closed after a WebGL context reset.
      }
    }
  }

  function updateFpsCounter(now, forceOffline) {
    if (!fpsCounter) {
      return;
    }
    if (forceOffline || !lastDeliveredFrameAt || now - lastDeliveredFrameAt > 1500) {
      fpsCounter.textContent = "HP -- FPS";
      fpsCounter.classList.remove("live");
      return;
    }
    while (deliveredFrameTimes.length > 1 && deliveredFrameTimes[0] < now - 1000) {
      deliveredFrameTimes.shift();
    }
    if (deliveredFrameTimes.length < 2) {
      return;
    }
    var elapsed = deliveredFrameTimes[deliveredFrameTimes.length - 1] - deliveredFrameTimes[0];
    if (elapsed <= 0) {
      return;
    }
    var measuredFps = (deliveredFrameTimes.length - 1) * 1000 / elapsed;
    fpsCounter.textContent = "HP " + Math.round(measuredFps) + " FPS";
    fpsCounter.classList.add("live");
  }

  function recordDeliveredFrame() {
    var now = nowMilliseconds();
    lastDeliveredFrameAt = now;
    deliveredFrameTimes.push(now);
    while (deliveredFrameTimes.length > 1 && deliveredFrameTimes[0] < now - 1000) {
      deliveredFrameTimes.shift();
    }
    if (now - lastFpsCounterUpdateAt >= 250) {
      lastFpsCounterUpdateAt = now;
      updateFpsCounter(now, false);
    }
  }

  function setCurrentFrame(source, sequence, ageMs) {
    var width = frameSourceWidth(source);
    var height = frameSourceHeight(source);
    if (!width || !height) {
      closeFrameSource(source);
      return false;
    }
    if (currentFrame && currentFrame.source !== source) {
      closeFrameSource(currentFrame.source);
    }
    currentFrameRevision += 1;
    currentFrame = {
      source: source,
      width: width,
      height: height,
      sequence: sequence,
      revision: currentFrameRevision
    };
    latestFrameAgeMs = Number.isFinite(ageMs) ? Math.max(0, Math.round(ageMs)) : null;
    renderDirty = true;
    recordDeliveredFrame();
    return true;
  }

  function decodeFrameBlob(blob) {
    if (typeof window.createImageBitmap === "function") {
      return window.createImageBitmap(blob);
    }
    return new Promise(function (resolve, reject) {
      if (!window.URL || !window.URL.createObjectURL) {
        reject(new Error("Browser tidak mendukung decode frame rendah-latensi."));
        return;
      }
      var objectUrl = window.URL.createObjectURL(blob);
      var image = new Image();
      image.onload = function () {
        window.URL.revokeObjectURL(objectUrl);
        resolve(image);
      };
      image.onerror = function () {
        window.URL.revokeObjectURL(objectUrl);
        reject(new Error("JPEG frame tidak dapat didecode."));
      };
      image.src = objectUrl;
    });
  }

  function supportsLatestFrameTransport() {
    return typeof window.fetch === "function" && (
      typeof window.createImageBitmap === "function" ||
      Boolean(window.URL && window.URL.createObjectURL)
    );
  }

  function numberFormat(value, control) {
    var decimals = Number(control.dataset.decimals || 0);
    return Number(value).toFixed(decimals) + (control.dataset.unit || "");
  }

  function fitModeValue(fit) {
    if (fit === "cover") {
      return 1;
    }
    if (fit === "stretch") {
      return 2;
    }
    return 0;
  }

  function renderPixelRatio() {
    var deviceRatio = Math.max(1, Number(window.devicePixelRatio) || 1);
    var headset = settings && settings.headset ? settings.headset : null;
    var maximum = headset && headset.nativeResolution ? 3 : 2;
    return Math.min(deviceRatio, maximum);
  }

  function updateRenderMeta() {
    var node = document.getElementById("phoneRenderMeta");
    if (!node) {
      return;
    }
    var ratio = renderPixelRatio();
    var width = Math.max(1, Math.floor(window.innerWidth * ratio));
    var height = Math.max(1, Math.floor(window.innerHeight * ratio));
    var nativeEnabled = Boolean(settings && settings.headset && settings.headset.nativeResolution);
    node.textContent = nativeEnabled
      ? "Tajam aktif: canvas " + width + " x " + height + " pada DPR " + ratio.toFixed(0) + "."
      : "Mode seimbang: canvas " + width + " x " + height + " pada DPR " + ratio.toFixed(0) + ".";
  }

  function rangeFill(control) {
    var minimum = Number(control.min);
    var maximum = Number(control.max);
    var fill = (Number(control.value) - minimum) / (maximum - minimum) * 100;
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
        rangeFill(control);
      }
    });
    outputNodes.forEach(function (output) {
      var control = document.querySelector("[data-path='" + output.dataset.output + "']");
      output.textContent = numberFormat(getPath(settings, output.dataset.output), control);
    });
    updateRenderMeta();
    renderDirty = true;
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
        stateLabel.textContent = "Pengaturan belum sampai ke PC";
      });
    }, 160);
  }

  controls.forEach(function (control) {
    var eventName = control.type === "checkbox" ? "change" : "input";
    control.addEventListener(eventName, function () {
      if (!settings) {
        return;
      }
      var value = control.type === "checkbox" ? control.checked : Number(control.value);
      setPath(settings, control.dataset.path, value);
      lastLocalChange = Date.now();
      refreshControls();
      queueSave();
    });
  });

  function compileShader(gl, type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) || "Shader compile failed");
    }
    return shader;
  }

  function createWebGlRenderer() {
    var gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      desynchronized: true,
      powerPreference: "high-performance",
      preserveDrawingBuffer: false
    });
    if (!gl) {
      return null;
    }

    try {
      var program = gl.createProgram();
      gl.attachShader(program, compileShader(gl, gl.VERTEX_SHADER, vertexShaderSource));
      gl.attachShader(program, compileShader(gl, gl.FRAGMENT_SHADER, fragmentShaderSource));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(program) || "Shader link failed");
      }

      var buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
        -1, -1, 1, -1, -1, 1,
        -1, 1, 1, -1, 1, 1
      ]), gl.STATIC_DRAW);

      var texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        1,
        1,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        new Uint8Array([0, 0, 0, 255])
      );

      var position = gl.getAttribLocation(program, "aPosition");
      var uniforms = {
        texture: gl.getUniformLocation(program, "uTexture"),
        crop: gl.getUniformLocation(program, "uCrop"),
        frame: gl.getUniformLocation(program, "uFrame"),
        sourceAspect: gl.getUniformLocation(program, "uSourceAspect"),
        eyeAspect: gl.getUniformLocation(program, "uEyeAspect"),
        fitMode: gl.getUniformLocation(program, "uFitMode"),
        gap: gl.getUniformLocation(program, "uGap"),
        offset: gl.getUniformLocation(program, "uOffset"),
        zoom: gl.getUniformLocation(program, "uZoom"),
        barrel: gl.getUniformLocation(program, "uBarrel"),
        curvature: gl.getUniformLocation(program, "uCurvature"),
        brightness: gl.getUniformLocation(program, "uBrightness"),
        flipVertical: gl.getUniformLocation(program, "uFlipVertical")
      };
      var uploadedFrameRevision = -1;
      var lastLegacyTextureUploadAt = 0;

      function resize() {
        var ratio = renderPixelRatio();
        var width = Math.max(1, Math.floor(window.innerWidth * ratio));
        var height = Math.max(1, Math.floor(window.innerHeight * ratio));
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width;
          canvas.height = height;
        }
        gl.viewport(0, 0, canvas.width, canvas.height);
      }

      function render() {
        resize();
        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        var frame = currentFrame;
        var now = nowMilliseconds();
        var captureFps = settings && settings.capture ? Number(settings.capture.fps) : 30;
        var legacyUploadInterval = 1000 / Math.max(5, Math.min(60, captureFps || 30));
        var legacyFrameNeedsUpload = usingMjpegFallback && frame &&
          now - lastLegacyTextureUploadAt >= legacyUploadInterval;
        if (frame && (frame.revision !== uploadedFrameRevision || legacyFrameNeedsUpload)) {
          try {
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, frame.source);
            uploadedFrameRevision = frame.revision;
            lastLegacyTextureUploadAt = now;
          } catch (error) {
            // The next animation frame retries if a browser replaces the fallback image mid-upload.
          }
        }
        var source = settings ? settings.source : { cropX: 0, cropY: 0, cropWidth: 100, cropHeight: 100 };
        var headset = settings ? settings.headset : {
          eyeWidth: 92, eyeHeight: 90, eyeGap: 2, eyeOffsetX: 0, eyeOffsetY: 0,
          zoom: 1, barrel: 0.12, curvature: 0.08, brightness: 1, flipVertical: true
        };
        var stream = settings && settings.stream ? settings.stream : { width: 16, height: 9 };
        var sourceWidth = frame ? frame.width : stream.width;
        var sourceHeight = frame ? frame.height : stream.height;
        var sourceAspect = (sourceWidth * source.cropWidth) /
          Math.max(1, sourceHeight * source.cropHeight);
        var eyeAspect = (canvas.width * headset.eyeWidth) /
          Math.max(1, canvas.height * 2 * headset.eyeHeight);
        gl.uniform1i(uniforms.texture, 0);
        gl.uniform4f(
          uniforms.crop,
          source.cropX / 100,
          source.cropY / 100,
          source.cropWidth / 100,
          source.cropHeight / 100
        );
        gl.uniform2f(uniforms.frame, headset.eyeWidth / 100, headset.eyeHeight / 100);
        gl.uniform1f(uniforms.sourceAspect, sourceAspect);
        gl.uniform1f(uniforms.eyeAspect, eyeAspect);
        gl.uniform1f(uniforms.fitMode, fitModeValue(source.fit));
        gl.uniform1f(uniforms.gap, headset.eyeGap / 100);
        gl.uniform2f(uniforms.offset, headset.eyeOffsetX / 100, headset.eyeOffsetY / 100);
        gl.uniform1f(uniforms.zoom, headset.zoom);
        gl.uniform1f(uniforms.barrel, headset.barrel);
        gl.uniform1f(uniforms.curvature, headset.curvature);
        gl.uniform1f(uniforms.brightness, headset.brightness);
        gl.uniform1f(uniforms.flipVertical, headset.flipVertical ? 1 : 0);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.flush();
      }

      return { render: render };
    } catch (error) {
      console.warn("WebGL renderer unavailable", error);
      return null;
    }
  }

  function createCanvasFallback() {
    var context = canvas.getContext("2d");

    function drawEye(image, sourceWidth, sourceHeight, crop, x, y, width, height, eyeSign, headset, fit) {
      context.save();
      context.beginPath();
      context.rect(x, y, width, height);
      context.clip();
      var cropWidth = sourceWidth * crop.cropWidth / 100;
      var cropHeight = sourceHeight * crop.cropHeight / 100;
      var cropX = sourceWidth * crop.cropX / 100;
      var cropY = sourceHeight * crop.cropY / 100;
      var imageWidth;
      var imageHeight;
      if (fit === "stretch") {
        imageWidth = width * headset.zoom;
        imageHeight = height * headset.zoom;
      } else {
        var scaleBase = fit === "cover" ? Math.max : Math.min;
        var scale = scaleBase(width / cropWidth, height / cropHeight) * headset.zoom;
        imageWidth = cropWidth * scale;
        imageHeight = cropHeight * scale;
      }
      var offsetX = eyeSign * headset.eyeOffsetX / 100 * width;
      var offsetY = headset.eyeOffsetY / 100 * height;
      var drawX = x + (width - imageWidth) / 2 + offsetX;
      var drawY = y + (height - imageHeight) / 2 + offsetY;
      context.filter = "brightness(" + headset.brightness + ")";
      if (headset.flipVertical) {
        context.translate(0, drawY * 2 + imageHeight);
        context.scale(1, -1);
      }
      context.drawImage(
        image,
        cropX,
        cropY,
        cropWidth,
        cropHeight,
        drawX,
        drawY,
        imageWidth,
        imageHeight
      );
      context.restore();
    }

    function render() {
      var ratio = renderPixelRatio();
      var width = Math.max(1, Math.floor(window.innerWidth * ratio));
      var height = Math.max(1, Math.floor(window.innerHeight * ratio));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.fillStyle = "#000";
      context.fillRect(0, 0, width, height);
      var frame = currentFrame;
      if (!frame || !settings) {
        return;
      }
      var headset = settings.headset;
      var cellWidth = width / 2;
      var frameWidth = cellWidth * headset.eyeWidth / 100;
      var frameHeight = height * headset.eyeHeight / 100;
      var gap = cellWidth * headset.eyeGap / 100;
      var y = (height - frameHeight) / 2;
      drawEye(
        frame.source,
        frame.width,
        frame.height,
        settings.source,
        (cellWidth - frameWidth) / 2 - gap / 2,
        y,
        frameWidth,
        frameHeight,
        -1,
        headset,
        settings.source.fit || "contain"
      );
      drawEye(
        frame.source,
        frame.width,
        frame.height,
        settings.source,
        cellWidth + (cellWidth - frameWidth) / 2 + gap / 2,
        y,
        frameWidth,
        frameHeight,
        1,
        headset,
        settings.source.fit || "contain"
      );
    }

    return { render: render };
  }

  function requestFullscreen() {
    var target = document.documentElement;
    if (target.requestFullscreen) {
      target.requestFullscreen()["catch"](function () {});
    }
    if (screen.orientation && screen.orientation.lock) {
      screen.orientation.lock("landscape")["catch"](function () {});
    }
    if (navigator.wakeLock && navigator.wakeLock.request) {
      navigator.wakeLock.request("screen").then(function (lock) {
        wakeLock = lock;
      })["catch"](function () {});
    }
    window.setTimeout(function () {
      renderDirty = true;
    }, 120);
  }

  function enterVr() {
    onboarding.classList.add("dismissed");
    requestFullscreen();
    chrome.classList.remove("hidden");
    window.setTimeout(function () {
      if (!tuneSheet.classList.contains("open")) {
        chrome.classList.add("hidden");
      }
    }, 2600);
  }

  function setTuneOpen(open) {
    tuneSheet.classList.toggle("open", open);
    chrome.classList.toggle("hidden", !open);
  }

  document.getElementById("enterVrButton").addEventListener("click", enterVr);
  document.getElementById("fullscreenButton").addEventListener("click", requestFullscreen);
  document.getElementById("tuneButton").addEventListener("click", function () {
    setTuneOpen(true);
  });
  document.getElementById("closeTuneButton").addEventListener("click", function () {
    setTuneOpen(false);
  });

  document.getElementById("vrStage").addEventListener("click", function () {
    if (!onboarding.classList.contains("dismissed") || tuneSheet.classList.contains("open")) {
      return;
    }
    chrome.classList.toggle("hidden");
  });

  function updateOrientationNotice() {
    orientationNotice.classList.toggle("hidden", window.innerWidth > window.innerHeight);
  }

  function updateMobileStatus() {
    requestJson("/api/status").then(function (status) {
      updateFpsCounter(nowMilliseconds(), false);
      if (status.capture.error) {
        stateLabel.textContent = "Capture PC error: " + status.capture.error;
      } else if (status.capture.sequence > 0) {
        stateLabel.textContent = "Desktop live - " + status.capture.measuredFps + " fps" +
          (latestFrameAgeMs === null ? "" : " / frame terbaru ~" + latestFrameAgeMs + " ms");
      } else {
        stateLabel.textContent = "Menunggu capture desktop...";
      }
    })["catch"](function () {
      updateFpsCounter(nowMilliseconds(), true);
      stateLabel.textContent = "Tidak dapat menjangkau PC. Cek USB atau Wi-Fi.";
    });
  }

  function pollSettings() {
    requestJson("/api/settings").then(function (serverSettings) {
      if (Date.now() - lastLocalChange > 900) {
        settings = serverSettings;
        refreshControls();
      }
    })["catch"](function () {});
  }

  function startMjpegFallback() {
    usingMjpegFallback = true;
    streamImage.onload = function () {
      frameSequence += 1;
      setCurrentFrame(streamImage, frameSequence, null);
      window.clearTimeout(retryTimer);
    };
    streamImage.onerror = function () {
      stateLabel.textContent = "Stream terputus, mencoba lagi...";
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(startMjpegFallback, 1200);
    };
    streamImage.src = "/stream.mjpg?role=phone&start=" + Date.now();
  }

  function requestLatestFrame() {
    if (frameTransportStopped || frameRequestRunning) {
      return;
    }
    frameRequestRunning = true;
    var controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
    frameRequestController = controller;
    var requestOptions = { cache: "no-store" };
    if (controller) {
      requestOptions.signal = controller.signal;
    }
    var responseReceivedAt = 0;
    window.fetch(
      "/frame.jpg?role=phone&after=" + encodeURIComponent(frameSequence),
      requestOptions
    ).then(function (response) {
      responseReceivedAt = nowMilliseconds();
      if (response.status === 204) {
        return null;
      }
      if (!response.ok) {
        throw new Error("HTTP " + response.status);
      }
      var sequence = Number(response.headers.get("X-LensCast-Sequence"));
      if (!Number.isFinite(sequence)) {
        sequence = frameSequence + 1;
      }
      var streamReset = response.headers.get("X-LensCast-Stream-Reset") === "1";
      var serverAgeMs = Number(response.headers.get("X-LensCast-Frame-Age-Ms"));
      if (!Number.isFinite(serverAgeMs)) {
        serverAgeMs = 0;
      }
      return response.blob().then(function (blob) {
        return decodeFrameBlob(blob);
      }).then(function (source) {
        return {
          source: source,
          sequence: sequence,
          streamReset: streamReset,
          ageMs: serverAgeMs + Math.max(0, nowMilliseconds() - responseReceivedAt)
        };
      });
    }).then(function (frame) {
      frameRequestRunning = false;
      frameRequestController = null;
      if (frame) {
        if (frame.streamReset || frame.sequence > frameSequence) {
          if (setCurrentFrame(frame.source, frame.sequence, frame.ageMs)) {
            frameSequence = frame.sequence;
            window.clearTimeout(retryTimer);
          }
        } else {
          closeFrameSource(frame.source);
        }
      }
      if (!frameTransportStopped) {
        requestLatestFrame();
      }
    })["catch"](function (error) {
      frameRequestRunning = false;
      frameRequestController = null;
      if (frameTransportStopped || (error && error.name === "AbortError")) {
        return;
      }
      stateLabel.textContent = "Stream terputus, mencoba lagi...";
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(requestLatestFrame, 350);
    });
  }

  function startStream() {
    if (!supportsLatestFrameTransport()) {
      startMjpegFallback();
      return;
    }
    stateLabel.textContent = "Menghubungkan mode latensi rendah...";
    usingMjpegFallback = false;
    frameTransportStopped = false;
    requestLatestFrame();
  }

  function loop(timestamp) {
    var now = Number(timestamp);
    if (!Number.isFinite(now)) {
      now = nowMilliseconds();
    }
    var captureFps = settings && settings.capture ? Number(settings.capture.fps) : 30;
    var keepAliveFps = Math.max(15, Math.min(60, captureFps || 30));
    if (renderer && (renderDirty || now >= nextKeepAliveAt)) {
      renderer.render();
      renderDirty = false;
      nextKeepAliveAt = now + 1000 / keepAliveFps;
    }
    window.requestAnimationFrame(loop);
  }

  function boot() {
    renderer = createWebGlRenderer() || createCanvasFallback();
    requestJson("/api/settings").then(function (initialSettings) {
      settings = initialSettings;
      refreshControls();
      updateMobileStatus();
    })["catch"](function () {
      stateLabel.textContent = "Tidak dapat memuat konfigurasi dari PC";
    });
    startStream();
    pollingTimer = window.setInterval(pollSettings, 1200);
    statusTimer = window.setInterval(updateMobileStatus, 2000);
    window.addEventListener("resize", function () {
      updateOrientationNotice();
      updateRenderMeta();
      renderDirty = true;
    });
    window.addEventListener("orientationchange", function () {
      updateOrientationNotice();
      updateRenderMeta();
      renderDirty = true;
    });
    updateOrientationNotice();
    loop();
  }

  window.addEventListener("beforeunload", function () {
    window.clearTimeout(saveTimer);
    window.clearTimeout(retryTimer);
    window.clearInterval(pollingTimer);
    window.clearInterval(statusTimer);
    frameTransportStopped = true;
    if (frameRequestController) {
      frameRequestController.abort();
    }
    if (currentFrame) {
      closeFrameSource(currentFrame.source);
      currentFrame = null;
    }
    if (wakeLock) {
      wakeLock.release()["catch"](function () {});
    }
  });

  boot();
}());
