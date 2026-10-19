package org.meumeu.wivrn;

public class MainActivity extends android.app.NativeActivity
{
	private volatile boolean microphoneActivityResumed = false;
	private final java.util.concurrent.atomic.AtomicInteger microphoneCaptureEpoch = new java.util.concurrent.atomic.AtomicInteger();

	@Override
	protected void onResume()
	{
		super.onResume();
		microphoneCaptureEpoch.incrementAndGet();
		microphoneActivityResumed = true;
	}

	@Override
	protected void onPause()
	{
		microphoneActivityResumed = false;
		microphoneCaptureEpoch.incrementAndGet();
		super.onPause();
	}

	public int getMicrophoneCaptureEpoch()
	{
		return microphoneCaptureEpoch.get();
	}

	static
	{
		System.loadLibrary("wivrn");
	}

	@Override
	public native void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults);

	@Override
	public native void onNewIntent(android.content.Intent intent);

	@Override
    public void onActivityResult(int requestCode, int resultCode, android.content.Intent data) {
        if (requestCode != 0x4846) { onNativeActivityResult(requestCode, resultCode, data); return; }
        long id = capturePrompt; capturePrompt = 0;
        if (id == 0 || id != HeadsetCaptureService.requestedId) return;
        if (resultCode != RESULT_OK || data == null) {
            HeadsetCaptureService.status = "Screen sharing was declined; reselect Headset view to try again";
            return;
        }
        android.content.Intent service = new android.content.Intent(this, HeadsetCaptureService.class);
        service.putExtra("id", id).putExtra("result", resultCode).putExtra("data", data);
        startForegroundService(service);
    }
    public native void onNativeActivityResult(int requestCode, int resultCode, android.content.Intent data);
    private long capturePrompt, captureAsked;
    public void renewHeadsetCapture(long id, boolean frames) {
        runOnUiThread(() -> {
            HeadsetCaptureService.renew(id, frames);
            if (HeadsetCaptureService.requestedId != id || captureAsked == id || capturePrompt != 0) return;
            captureAsked = id; capturePrompt = id;
            HeadsetCaptureService.status = "Accept screen-sharing permission in the headset";
            try {
                android.media.projection.MediaProjectionManager manager = getSystemService(android.media.projection.MediaProjectionManager.class);
                startActivityForResult(manager.createScreenCaptureIntent(), 0x4846);
            } catch (RuntimeException error) { capturePrompt = 0; HeadsetCaptureService.status = "Screen sharing: " + error.getMessage(); }
        });
    }
    public void stopHeadsetCapture() { runOnUiThread(() -> HeadsetCaptureService.disable(this)); }
    public byte[] pollHeadsetCapture() { return HeadsetCaptureService.latest.getAndSet(null); }
    public String getHeadsetCaptureStatus() { return HeadsetCaptureService.status; }
    @Override protected void onDestroy() { HeadsetCaptureService.disable(this); super.onDestroy(); }


	// Called by the native audio owner. Query only; never change system privacy.
	public int getMicrophoneCaptureBlockReason()
	{
		try
		{
			if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED)
				return 1;
			android.media.AudioManager audio = getSystemService(android.media.AudioManager.class);
			if (audio == null || audio.isMicrophoneMute())
				return 2;
			if (!microphoneActivityResumed)
				return 4;
			return 0;
		}
		catch (RuntimeException error)
		{
			return 8; // Failed queries must not enable capture or recovery retries.
		}
	}

	private android.content.BroadcastReceiver BatteryInfoReceiver = null;

	@Override
	protected void onCreate(android.os.Bundle savedInstanceState)
	{
		super.onCreate(savedInstanceState);

		this.BatteryInfoReceiver = new BroadcastReceiver();

		this.registerReceiver(this.BatteryInfoReceiver, new android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED));
	}
}
