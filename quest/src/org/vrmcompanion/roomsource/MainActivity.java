package org.vrmcompanion.roomsource;
import android.app.Activity;
import android.os.Bundle;
import android.content.pm.PackageManager;
import android.view.WindowManager;
import android.widget.TextView;
public class MainActivity extends Activity {
    static { System.loadLibrary("openxr_loader"); System.loadLibrary("questsource"); }
    private boolean started = false;
    private native void nativeStart(int port, String token, String directory, boolean calibration);
    private native void nativeLifecycle(boolean resumed, boolean stop);
    private void startSource() {
        if (started) return;
        started = true;
        nativeStart(getIntent().getIntExtra("port",47845), getIntent().getStringExtra("token"), getFilesDir().toString(), getIntent().getBooleanExtra("calibration",false));
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        TextView text = new TextView(this);
        text.setText("Companion Room Source\nGuided playspace capture\nFollow four directions in passthrough to save up to twelve markers.\nHold still when prompted. X retries; A skips.\nA final pass reloads and checks the saved markers.\nRoom access is optional; anchors are app-owned.");
        text.setTextSize(24); setContentView(text);
        if (checkSelfPermission("com.oculus.permission.USE_SCENE") != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{"com.oculus.permission.USE_SCENE"}, 1);
        else startSource();
    }
    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        startSource();
    }
    @Override public void onResume() { super.onResume(); nativeLifecycle(true, false); }
    @Override public void onPause() { nativeLifecycle(false, false); super.onPause(); }
    @Override public void onDestroy() { nativeLifecycle(false, true); super.onDestroy(); }
}
