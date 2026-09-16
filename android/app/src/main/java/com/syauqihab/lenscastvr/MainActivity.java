package com.syauqihab.lenscastvr;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ActivityInfo;
import android.content.pm.ApplicationInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;

/** Native Android shell for the LensCast phone renderer served by the PC. */
public final class MainActivity extends Activity {
    private static final String PREFERENCES = "lenscast_vr";
    private static final String ENDPOINT_KEY = "endpoint";
    private static final String DEFAULT_ENDPOINT = "http://127.0.0.1:8264/phone";
    private static final String LOCAL_VIDEO_URL = "file:///android_asset/video_vr.html";
    private static final int VIDEO_PICKER_REQUEST = 4102;
    private static final long CONNECTION_CHIP_TIMEOUT_MS = 4_000L;
    private static final long RECONNECT_DELAY_MS = 2_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable hideConnectionChip = this::hideConnectionChip;
    private final Runnable retryConnection = this::loadLensCast;

    private WebView webView;
    private Button connectionButton;
    private Button modeButton;
    private ValueCallback<Uri[]> videoFileCallback;
    private boolean mainFrameLoadFailed;
    private boolean localVideoMode;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.lenscast_webview);
        connectionButton = findViewById(R.id.connection_button);
        modeButton = findViewById(R.id.mode_button);
        modeButton.setOnClickListener(view -> showModeDialog());
        connectionButton.setOnClickListener(view -> {
            if (mainFrameLoadFailed) {
                loadLensCast();
            } else {
                showEndpointDialog();
            }
        });
        webView.setOnLongClickListener(view -> {
            showModeDialog();
            return true;
        });

        configureWebView();
        updateConnectionButton();
        loadLensCast();
        enterImmersiveMode();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);

        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        WebView.setWebContentsDebuggingEnabled(
                (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0
        );
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(
                    WebView view,
                    ValueCallback<Uri[]> filePathCallback,
                    FileChooserParams fileChooserParams
            ) {
                if (videoFileCallback != null) {
                    videoFileCallback.onReceiveValue(null);
                }
                videoFileCallback = filePathCallback;

                Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("video/*");
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                intent.addFlags(Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
                try {
                    startActivityForResult(intent, VIDEO_PICKER_REQUEST);
                } catch (android.content.ActivityNotFoundException error) {
                    videoFileCallback.onReceiveValue(null);
                    videoFileCallback = null;
                    return false;
                }
                return true;
            }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                if (!localVideoMode) {
                    mainFrameLoadFailed = false;
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (localVideoMode) {
                    connectionButton.setVisibility(View.GONE);
                    return;
                }
                if (!mainFrameLoadFailed) {
                    handler.removeCallbacks(retryConnection);
                    showConnectionChip(false);
                }
            }

            @Override
            public void onReceivedError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceError error
            ) {
                if (localVideoMode) {
                    return;
                }
                if (request.isForMainFrame()) {
                    mainFrameLoadFailed = true;
                    connectionButton.setText("PC belum terhubung - coba lagi");
                    showConnectionChip(true);
                    handler.removeCallbacks(retryConnection);
                    handler.postDelayed(retryConnection, RECONNECT_DELAY_MS);
                }
            }
        });
    }

    private void loadLensCast() {
        handler.removeCallbacks(retryConnection);
        localVideoMode = false;
        mainFrameLoadFailed = false;
        updateConnectionButton();
        connectionButton.setVisibility(View.VISIBLE);
        modeButton.setText("Mode: PC");
        webView.loadUrl(getEndpoint());
        showConnectionChip(false);
    }

    private void loadLocalVideo() {
        handler.removeCallbacks(retryConnection);
        localVideoMode = true;
        mainFrameLoadFailed = false;
        connectionButton.setVisibility(View.GONE);
        modeButton.setText("Mode: Video");
        webView.loadUrl(LOCAL_VIDEO_URL);
        enterImmersiveMode();
    }

    private void showModeDialog() {
        String[] modes = {"PC Mirror", "Video dari penyimpanan HP"};
        int selected = localVideoMode ? 1 : 0;
        new AlertDialog.Builder(this)
                .setTitle("Pilih mode LensCast")
                .setSingleChoiceItems(modes, selected, (dialog, which) -> {
                    dialog.dismiss();
                    if (which == 1) {
                        loadLocalVideo();
                    } else {
                        loadLensCast();
                    }
                })
                .setNegativeButton("Batal", null)
                .show();
    }

    private String getEndpoint() {
        return getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .getString(ENDPOINT_KEY, DEFAULT_ENDPOINT);
    }

    private void showEndpointDialog() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setSelectAllOnFocus(true);
        input.setText(getEndpoint());
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(Color.LTGRAY);
        input.setHint("http://127.0.0.1:8264/phone");
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);

        int margin = dp(24);
        FrameLayout container = new FrameLayout(this);
        container.setPadding(margin, 0, margin, 0);
        container.addView(input, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT
        ));

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Koneksi PC LensCast")
                .setMessage("USB memakai http://127.0.0.1:8264/phone. Tekan Connect USB di PC; masukkan alamat lain hanya bila memakai koneksi berbeda.")
                .setView(container)
                .setNegativeButton("Batal", null)
                .setPositiveButton("Simpan", null)
                .create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(view -> {
                    String endpoint = normalizeEndpoint(input.getText().toString());
                    if (endpoint == null) {
                        input.setError("Masukkan URL HTTP yang valid");
                        return;
                    }
                    getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                            .edit()
                            .putString(ENDPOINT_KEY, endpoint)
                            .apply();
                    dialog.dismiss();
                    loadLensCast();
                }));
        dialog.show();
        input.requestFocus();
    }

    private String normalizeEndpoint(String rawEndpoint) {
        String endpoint = rawEndpoint == null ? "" : rawEndpoint.trim();
        if (endpoint.isEmpty()) {
            return DEFAULT_ENDPOINT;
        }
        if (!endpoint.startsWith("http://") && !endpoint.startsWith("https://")) {
            endpoint = "http://" + endpoint;
        }
        Uri parsed = Uri.parse(endpoint);
        String scheme = parsed.getScheme();
        if (parsed.getHost() == null
                || (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme))) {
            return null;
        }
        String path = parsed.getPath();
        if (path == null || path.isEmpty() || "/".equals(path)) {
            return parsed.buildUpon().path("/phone").build().toString();
        }
        return parsed.toString();
    }

    private void updateConnectionButton() {
        Uri endpoint = Uri.parse(getEndpoint());
        String host = endpoint.getHost() == null ? "PC" : endpoint.getHost();
        int port = endpoint.getPort();
        connectionButton.setText(port > 0 ? "PC: " + host + ":" + port : "PC: " + host);
    }

    private void showConnectionChip(boolean persistent) {
        handler.removeCallbacks(hideConnectionChip);
        connectionButton.animate().cancel();
        connectionButton.setVisibility(View.VISIBLE);
        connectionButton.setAlpha(1f);
        if (!persistent) {
            handler.postDelayed(hideConnectionChip, CONNECTION_CHIP_TIMEOUT_MS);
        }
    }

    private void hideConnectionChip() {
        connectionButton.animate()
                .alpha(0f)
                .setDuration(180L)
                .withEndAction(() -> connectionButton.setVisibility(View.INVISIBLE))
                .start();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != VIDEO_PICKER_REQUEST || videoFileCallback == null) {
            return;
        }

        Uri[] result = null;
        if (resultCode == RESULT_OK && data != null) {
            Uri uri = data.getData();
            if (uri != null) {
                try {
                    getContentResolver().takePersistableUriPermission(
                            uri,
                            Intent.FLAG_GRANT_READ_URI_PERMISSION
                    );
                } catch (SecurityException ignored) {
                    // The temporary picker grant remains valid for this playback session.
                }
                result = new Uri[]{uri};
            } else {
                ClipData clipData = data.getClipData();
                if (clipData != null && clipData.getItemCount() > 0) {
                    result = new Uri[]{clipData.getItemAt(0).getUri()};
                }
            }
        }
        videoFileCallback.onReceiveValue(result);
        videoFileCallback = null;
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            enterImmersiveMode();
        }
    }

    @SuppressWarnings("deprecation")
    private void enterImmersiveMode() {
        Window window = getWindow();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false);
            WindowInsetsController controller = window.getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(
                        WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                );
            }
            return;
        }
        window.getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(retryConnection);
        webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
        enterImmersiveMode();
        if (!localVideoMode && mainFrameLoadFailed) {
            loadLensCast();
        }
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (videoFileCallback != null) {
            videoFileCallback.onReceiveValue(null);
            videoFileCallback = null;
        }
        webView.stopLoading();
        webView.destroy();
        super.onDestroy();
    }
}
