package org.meumeu.wivrn;

public class MainActivity extends android.app.NativeActivity
{
	public Object createCodecTestDecoder(int width, int height) throws Exception { return new CodecTestDecoder(width, height); }

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
	public native void onActivityResult(int requestCode, int resultCode, android.content.Intent data);

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
