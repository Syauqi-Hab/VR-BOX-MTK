(function () {
  "use strict";

  var STORAGE_KEY = "lenscast-local-video-settings-v1";
  var defaults = {
    screenSize: 88,
    eyeGap: 4,
    zoom: 100,
    barrel: 8,
    brightness: 100,
    sourceMode: "flat",
    fitMode: "contain"
  };

  var canvas = document.getElementById("vrCanvas");
  var video = document.getElementById("sourceVideo");
  var videoInput = document.getElementById("videoInput");
  var welcomeCard = document.getElementById("welcomeCard");
  var welcomeStatus = document.getElementById("welcomeStatus");
  var playerChrome = document.getElementById("playerChrome");
  var settingsSheet = document.getElementById("settingsSheet");
  var videoName = document.getElementById("videoName");
  var videoMeta = document.getElementById("videoMeta");
  var sourceMode = document.getElementById("sourceMode");
  var fitMode = document.getElementById("fitMode");
  var playButton = document.getElementById("playButton");
  var seekBar = document.getElementById("seekBar");
  var currentTimeNode = document.getElementById("currentTime");
  var durationNode = document.getElementById("duration");
  var tapHint = document.getElementById("tapHint");
  var settingInputs = Array.prototype.slice.call(document.querySelectorAll("[data-setting]"));
  var settingOutputs = Array.prototype.slice.call(document.querySelectorAll("[data-output]"));
  var objectUrl = null;
  var hideTimer = null;
  var renderer = null;
  var settings = loadSettings();

  function loadSettings() {
    try {
      var stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      return Object.assign({}, defaults, stored || {});
    } catch (error) {
      return Object.assign({}, defaults);
    }
  }

  function saveSettings() {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    var minutes = Math.floor(seconds / 60);
    var remainder = Math.floor(seconds % 60);
    return String(minutes) + ":" + String(remainder).padStart(2, "0");
  }

  function refreshSettingsUi() {
    settingInputs.forEach(function (input) {
      input.value = settings[input.dataset.setting];
    });
    settingOutputs.forEach(function (output) {
      var key = output.dataset.output;
      var value = Number(settings[key]);
      if (key === "barrel") {
        output.textContent = (value / 100).toFixed(2);
      } else if (key === "zoom" || key === "brightness") {
        output.textContent = (value / 100).toFixed(2) + "x";
      } else {
        output.textContent = String(value) + "%";
      }
    });
    sourceMode.value = settings.sourceMode;
    fitMode.value = settings.fitMode;
  }

  function compileShader(gl, type, source) {
    var shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) || "Shader gagal dikompilasi");
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
    if (!gl) return null;

    var vertexSource = [
      "attribute vec2 aPosition;",
      "varying vec2 vUv;",
      "void main() {",
      "  vUv = aPosition * 0.5 + 0.5;",
      "  gl_Position = vec4(aPosition, 0.0, 1.0);",
      "}"
    ].join("\n");

    var fragmentSource = [
      "precision highp float;",
      "uniform sampler2D uTexture;",
      "uniform vec2 uFrame;",
      "uniform float uGap;",
      "uniform float uZoom;",
      "uniform float uBarrel;",
      "uniform float uBrightness;",
      "uniform float uSourceAspect;",
      "uniform float uEyeAspect;",
      "uniform float uFitMode;",
      "uniform float uSourceMode;",
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
      "  float radiusSquared = dot(point, point);",
      "  point *= 1.0 + uBarrel * radiusSquared;",
      "  if (abs(point.x) > 0.5 || abs(point.y) > 0.5) {",
      "    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);",
      "    return;",
      "  }",
      "  vec2 displaySize = vec2(1.0);",
      "  if (uFitMode < 0.5) {",
      "    if (uSourceAspect > uEyeAspect) displaySize.y = uEyeAspect / uSourceAspect;",
      "    else displaySize.x = uSourceAspect / uEyeAspect;",
      "  } else if (uFitMode < 1.5) {",
      "    if (uSourceAspect > uEyeAspect) displaySize.x = uSourceAspect / uEyeAspect;",
      "    else displaySize.y = uEyeAspect / uSourceAspect;",
      "  }",
      "  displaySize *= uZoom;",
      "  vec2 sourcePoint = (point + 0.5 - (vec2(1.0) - displaySize) * 0.5) / displaySize;",
      "  if (sourcePoint.x < 0.0 || sourcePoint.x > 1.0 || sourcePoint.y < 0.0 || sourcePoint.y > 1.0) {",
      "    gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);",
      "    return;",
      "  }",
      "  vec2 sourceUv = sourcePoint;",
      "  if (uSourceMode > 0.5 && uSourceMode < 1.5) {",
      "    sourceUv.x = sourcePoint.x * 0.5 + eyeIndex * 0.5;",
      "  } else if (uSourceMode >= 1.5) {",
      "    sourceUv.y = sourcePoint.y * 0.5 + (1.0 - eyeIndex) * 0.5;",
      "  }",
      "  vec3 color = texture2D(uTexture, sourceUv).rgb * uBrightness;",
      "  gl_FragColor = vec4(color, 1.0);",
      "}"
    ].join("\n");

    try {
      var program = gl.createProgram();
      gl.attachShader(program, compileShader(gl, gl.VERTEX_SHADER, vertexSource));
      gl.attachShader(program, compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(gl.getProgramInfoLog(program) || "Program WebGL gagal ditautkan");
      }

      var buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
        -1, -1, 1, -1, -1, 1,
        -1, 1, 1, -1, 1, 1
      ]), gl.STATIC_DRAW);

      var position = gl.getAttribLocation(program, "aPosition");
      var texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(
        gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0,
        gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255])
      );
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);

      var uniforms = {
        frame: gl.getUniformLocation(program, "uFrame"),
        gap: gl.getUniformLocation(program, "uGap"),
        zoom: gl.getUniformLocation(program, "uZoom"),
        barrel: gl.getUniformLocation(program, "uBarrel"),
        brightness: gl.getUniformLocation(program, "uBrightness"),
        sourceAspect: gl.getUniformLocation(program, "uSourceAspect"),
        eyeAspect: gl.getUniformLocation(program, "uEyeAspect"),
        fitMode: gl.getUniformLocation(program, "uFitMode"),
        sourceMode: gl.getUniformLocation(program, "uSourceMode")
      };

      return function render() {
        resizeCanvas();
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clearColor(0, 0, 0, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
        if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;

        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        try {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
        } catch (error) {
          videoMeta.textContent = "Frame video belum dapat dirender";
          return;
        }

        var sourceAspect = video.videoWidth / video.videoHeight;
        if (settings.sourceMode === "sbs") sourceAspect *= 0.5;
        if (settings.sourceMode === "ou") sourceAspect *= 2;
        var size = clamp(Number(settings.screenSize) / 100, 0.5, 1);
        var fitValue = settings.fitMode === "cover" ? 1 : settings.fitMode === "stretch" ? 2 : 0;
        var sourceValue = settings.sourceMode === "sbs" ? 1 : settings.sourceMode === "ou" ? 2 : 0;

        gl.uniform2f(uniforms.frame, size, size);
        gl.uniform1f(uniforms.gap, Number(settings.eyeGap) / 100);
        gl.uniform1f(uniforms.zoom, Number(settings.zoom) / 100);
        gl.uniform1f(uniforms.barrel, Number(settings.barrel) / 100);
        gl.uniform1f(uniforms.brightness, Number(settings.brightness) / 100);
        gl.uniform1f(uniforms.sourceAspect, sourceAspect);
        gl.uniform1f(uniforms.eyeAspect, (canvas.width * 0.5) / canvas.height);
        gl.uniform1f(uniforms.fitMode, fitValue);
        gl.uniform1f(uniforms.sourceMode, sourceValue);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      };
    } catch (error) {
      return null;
    }
  }

  function createCanvasRenderer() {
    var context = canvas.getContext("2d", { alpha: false });
    if (!context) return function () {};
    return function render() {
      resizeCanvas();
      context.fillStyle = "#000";
      context.fillRect(0, 0, canvas.width, canvas.height);
      if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;

      var sourceX = 0;
      var sourceY = 0;
      var sourceWidth = video.videoWidth;
      var sourceHeight = video.videoHeight;
      for (var eye = 0; eye < 2; eye += 1) {
        if (settings.sourceMode === "sbs") {
          sourceWidth = video.videoWidth / 2;
          sourceHeight = video.videoHeight;
          sourceX = eye * sourceWidth;
          sourceY = 0;
        } else if (settings.sourceMode === "ou") {
          sourceWidth = video.videoWidth;
          sourceHeight = video.videoHeight / 2;
          sourceX = 0;
          sourceY = eye * sourceHeight;
        } else {
          sourceX = 0;
          sourceY = 0;
          sourceWidth = video.videoWidth;
          sourceHeight = video.videoHeight;
        }

        var eyeWidth = canvas.width / 2;
        var size = Number(settings.screenSize) / 100;
        var targetWidth = eyeWidth * size;
        var targetHeight = canvas.height * size;
        var scale = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
        if (settings.fitMode === "cover") scale = Math.max(targetWidth / sourceWidth, targetHeight / sourceHeight);
        if (settings.fitMode === "stretch") {
          context.drawImage(video, sourceX, sourceY, sourceWidth, sourceHeight, eye * eyeWidth, 0, eyeWidth, canvas.height);
          continue;
        }
        targetWidth = sourceWidth * scale * Number(settings.zoom) / 100;
        targetHeight = sourceHeight * scale * Number(settings.zoom) / 100;
        var targetX = eye * eyeWidth + (eyeWidth - targetWidth) / 2;
        var targetY = (canvas.height - targetHeight) / 2;
        context.drawImage(video, sourceX, sourceY, sourceWidth, sourceHeight, targetX, targetY, targetWidth, targetHeight);
      }
    };
  }

  function resizeCanvas() {
    var ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    var width = Math.max(1, Math.floor(window.innerWidth * ratio));
    var height = Math.max(1, Math.floor(window.innerHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  function renderLoop() {
    renderer();
    if (Number.isFinite(video.duration) && video.duration > 0 && !seekBar.matches(":active")) {
      seekBar.value = String(Math.round(video.currentTime / video.duration * 1000));
    }
    currentTimeNode.textContent = formatTime(video.currentTime);
    window.requestAnimationFrame(renderLoop);
  }

  function showControls() {
    if (playerChrome.hidden) return;
    playerChrome.classList.remove("hidden");
    tapHint.classList.remove("visible");
    window.clearTimeout(hideTimer);
    if (!video.paused && settingsSheet.hidden) {
      hideTimer = window.setTimeout(hideControls, 3200);
    }
  }

  function hideControls() {
    if (video.paused || !settingsSheet.hidden) return;
    playerChrome.classList.add("hidden");
    tapHint.classList.add("visible");
    window.setTimeout(function () { tapHint.classList.remove("visible"); }, 1400);
  }

  function updatePlaybackUi() {
    playButton.textContent = video.paused ? "▶" : "❚❚";
    videoMeta.textContent = video.videoWidth && video.videoHeight
      ? String(video.videoWidth) + " × " + String(video.videoHeight) + " · " + (video.paused ? "Dijeda" : "Diputar")
      : "Menyiapkan video...";
    if (video.paused) showControls();
    else showControls();
  }

  function selectVideo(file) {
    if (!file) return;
    if (objectUrl) window.URL.revokeObjectURL(objectUrl);
    objectUrl = window.URL.createObjectURL(file);
    videoName.textContent = file.name || "Video lokal";
    welcomeStatus.textContent = "Membuka " + (file.name || "video") + "...";
    video.src = objectUrl;
    video.load();
  }

  videoInput.addEventListener("change", function () {
    selectVideo(videoInput.files && videoInput.files[0]);
  });

  video.addEventListener("loadedmetadata", function () {
    welcomeCard.hidden = true;
    playerChrome.hidden = false;
    durationNode.textContent = formatTime(video.duration);
    updatePlaybackUi();
    video.play()["catch"](function () {
      videoMeta.textContent = "Siap diputar";
    });
  });

  video.addEventListener("play", updatePlaybackUi);
  video.addEventListener("pause", updatePlaybackUi);
  video.addEventListener("ended", updatePlaybackUi);
  video.addEventListener("error", function () {
    welcomeCard.hidden = false;
    playerChrome.hidden = true;
    welcomeStatus.textContent = "Video tidak dapat dibuka. Coba format MP4 H.264/AAC.";
  });

  playButton.addEventListener("click", function () {
    if (video.paused) video.play()["catch"](function () {});
    else video.pause();
  });

  seekBar.addEventListener("input", function () {
    if (Number.isFinite(video.duration) && video.duration > 0) {
      video.currentTime = Number(seekBar.value) / 1000 * video.duration;
    }
  });

  document.getElementById("changeVideoButton").addEventListener("click", function () {
    videoInput.click();
  });

  document.getElementById("settingsButton").addEventListener("click", function () {
    settingsSheet.hidden = false;
    showControls();
  });

  document.getElementById("closeSettingsButton").addEventListener("click", function () {
    settingsSheet.hidden = true;
    showControls();
  });

  document.getElementById("resetSettingsButton").addEventListener("click", function () {
    settings = Object.assign({}, defaults, {
      sourceMode: settings.sourceMode,
      fitMode: settings.fitMode
    });
    saveSettings();
    refreshSettingsUi();
  });

  document.getElementById("recenterButton").addEventListener("click", function () {
    settings.screenSize = defaults.screenSize;
    settings.eyeGap = defaults.eyeGap;
    settings.zoom = defaults.zoom;
    settings.barrel = defaults.barrel;
    saveSettings();
    refreshSettingsUi();
  });

  sourceMode.addEventListener("change", function () {
    settings.sourceMode = sourceMode.value;
    saveSettings();
  });

  fitMode.addEventListener("change", function () {
    settings.fitMode = fitMode.value;
    saveSettings();
  });

  settingInputs.forEach(function (input) {
    input.addEventListener("input", function () {
      settings[input.dataset.setting] = Number(input.value);
      saveSettings();
      refreshSettingsUi();
    });
  });

  canvas.addEventListener("click", function () {
    if (playerChrome.hidden) return;
    if (playerChrome.classList.contains("hidden")) showControls();
    else hideControls();
  });

  window.addEventListener("resize", resizeCanvas);
  window.addEventListener("beforeunload", function () {
    if (objectUrl) window.URL.revokeObjectURL(objectUrl);
  });

  refreshSettingsUi();
  resizeCanvas();
  renderer = createWebGlRenderer() || createCanvasRenderer();
  window.requestAnimationFrame(renderLoop);
}());
