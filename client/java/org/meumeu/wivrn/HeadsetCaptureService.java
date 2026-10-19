package org.meumeu.wivrn;

import android.app.*;
import android.content.*;
import android.graphics.*;
import android.hardware.display.*;
import android.media.*;
import android.media.projection.*;
import android.os.*;
import java.io.ByteArrayOutputStream;
import java.nio.*;
import java.util.concurrent.atomic.AtomicReference;

/** User-consented headset display sharing. No microphone or audio capture. */
public final class HeadsetCaptureService extends Service {
    static volatile long requestedId, renewed;
    static volatile boolean frames;
    static volatile String status = "Headset sharing is off";
    static final AtomicReference<byte[]> latest = new AtomicReference<>();
    private MediaProjection projection;
    private VirtualDisplay display;
    private ImageReader reader;
    private HandlerThread thread;
    private Handler handler;
    private long owner, lastFrame;
    private boolean closing;

    static void renew(long id, boolean active) {
        requestedId = id; frames = active; renewed = System.nanoTime();
        if (!active) latest.set(null);
    }
    static void disable(Context context) {
        requestedId = 0; frames = false; latest.set(null);
        context.stopService(new Intent(context, HeadsetCaptureService.class));
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || projection != null) { stopSelf(); return START_NOT_STICKY; }
        owner = intent.getLongExtra("id", 0);
        if (owner == 0 || owner != requestedId) { stopSelf(); return START_NOT_STICKY; }
        try {
            NotificationManager notifications = getSystemService(NotificationManager.class);
            notifications.createNotificationChannel(new NotificationChannel("headset-sharing", "Headset display sharing", NotificationManager.IMPORTANCE_LOW));
            Notification notice = new Notification.Builder(this, "headset-sharing")
                .setSmallIcon(android.R.drawable.ic_menu_camera).setContentTitle("WiVRn Hands Frame")
                .setContentText("Sharing the headset view with your paired PC. Switch to PC scene or disable Hands Frame to stop.")
                .setOngoing(true).build();
            startForeground(9410, notice);
            thread = new HandlerThread("headset-capture"); thread.start(); handler = new Handler(thread.getLooper());
            MediaProjectionManager manager = getSystemService(MediaProjectionManager.class);
            projection = manager.getMediaProjection(intent.getIntExtra("result", Activity.RESULT_CANCELED), intent.getParcelableExtra("data"));
            if (projection == null) throw new IllegalStateException("No screen-sharing permission");
            projection.registerCallback(new MediaProjection.Callback() {
                @Override public void onStop() { status = "Headset screen sharing was stopped; reselect the source to request permission again"; stopSelf(); }
            }, handler);
            reader = ImageReader.newInstance(1024, 1024, PixelFormat.RGBA_8888, 2);
            reader.setOnImageAvailableListener(this::image, handler);
            display = projection.createVirtualDisplay("WiVRn headset view", 1024, 1024, getResources().getDisplayMetrics().densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, reader.getSurface(), null, handler);
            status = "Headset view ready (includes passthrough and virtual overlays)";
            handler.post(new Runnable() {
                @Override public void run() {
                    if (requestedId != owner || System.nanoTime() - renewed > 2000000000L) { stopSelf(); return; }
                    handler.postDelayed(this, 250);
                }
            });
        } catch (RuntimeException error) { status = "Headset sharing: " + error.getMessage(); stopSelf(); }
        return START_NOT_STICKY;
    }
    private void image(ImageReader source) {
        try (Image image = source.acquireLatestImage()) {
            long now = System.nanoTime();
            if (image == null || closing || requestedId != owner || !frames || now - renewed > 2000000000L || now - lastFrame < 100000000L) return;
            lastFrame = now;
            Image.Plane plane = image.getPlanes()[0];
            if (plane.getPixelStride() != 4) throw new IllegalStateException("Unsupported headset image format");
            ByteBuffer pixels = plane.getBuffer();
            int width = image.getWidth(), height = image.getHeight();
            int[] colors = new int[width * height];
            for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x) {
                int at = y * plane.getRowStride() + x * 4;
                colors[y * width + x] = Color.rgb(pixels.get(at) & 255, pixels.get(at + 1) & 255, pixels.get(at + 2) & 255);
            }
            Bitmap bitmap = Bitmap.createBitmap(colors, width, height, Bitmap.Config.ARGB_8888);
            ByteArrayOutputStream jpeg = new ByteArrayOutputStream();
            try { bitmap.compress(Bitmap.CompressFormat.JPEG, 72, jpeg); } finally { bitmap.recycle(); }
            byte[] data = jpeg.toByteArray();
            if (data.length <= 256 * 1024 && requestedId == owner && frames) {
                ByteBuffer packet = ByteBuffer.allocate(data.length + 8).order(ByteOrder.LITTLE_ENDIAN);
                packet.putLong(now).put(data); latest.set(packet.array());
            }
        } catch (RuntimeException error) { status = "Headset capture: " + error.getMessage(); }
    }
    @Override public void onDestroy() {
        closing = true; latest.set(null);
        if (handler != null) handler.removeCallbacksAndMessages(null);
        if (display != null) display.release();
        if (reader != null) reader.close();
        if (projection != null) projection.stop();
        if (thread != null) thread.quitSafely();
        stopForeground(true); super.onDestroy();
    }
}
