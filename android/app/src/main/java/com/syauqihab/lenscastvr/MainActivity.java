package com.syauqihab.lenscastvr;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
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
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;

/** Native Android shell for the LensCast phone renderer served by the PC. */
public final class MainActivity extends Activity {
    private static final String PREFERENCES = "lenscast_vr";
    private static final String ENDPOINT_KEY = "endpoint";
    private static final String DEFAULT_ENDPOINT = "http://127.0.0.1:8264/phone";
    private static final long CONNECTION_CHIP_TIMEOUT_MS = 4_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable hideConnectionChip = this::hideConnectionChip;

    private WebView webView;
    private Button connectionButton;
    private boolean mainFrameLoadFailed;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
        setContentView(R.layout.activity_main);

        webView = findViewById(R.id.lenscast_webview);
        connectionButton = findViewById(R.id.connection_button);
        connectionButton.setOnClickListener(view -> showEndpointDialog());
        webView.setOnLongClickListener(view -> {
            showEndpointDialog();
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
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);

        webView.setLayerType(View.LAYER_TYPE_HARDWARE, null);
        WebView.setWebContentsDebuggingEnabled(
                (getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0
        );
        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                mainFrameLoadFailed = false;
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (!mainFrameLoadFailed) {
                    showConnectionChip(false);
                }
            }

            @Override
            public void onReceivedError(
                    WebView view,
                    WebResourceRequest request,
                    WebResourceError error
            ) {
                if (request.isForMainFrame()) {
                    mainFrameLoadFailed = true;
                    connectionButton.setText("PC belum terhubung - sentuh untuk atur");
                    showConnectionChip(true);
                }
            }
        });
    }

    private void loadLensCast() {
        mainFrameLoadFailed = false;
        updateConnectionButton();
        webView.loadUrl(getEndpoint());
        showConnectionChip(false);
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
                .setMessage("USB ADB memakai http://127.0.0.1:8264/phone. Masukkan alamat PC bila memakai koneksi lain.")
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
        webView.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        webView.onResume();
        enterImmersiveMode();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        webView.stopLoading();
        webView.destroy();
        super.onDestroy();
    }
}
