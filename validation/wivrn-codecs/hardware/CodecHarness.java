// SPDX-License-Identifier: MIT
// Developer-shell MediaCodec test. No Activity, Surface or display timing.
import android.graphics.Rect;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

public final class CodecHarness {
    static final String MIME = "video/hevc";
    static void write(String path, JSONObject value) throws Exception {
        try (FileOutputStream out = new FileOutputStream(path)) {
            out.write((value.toString(2) + "\n").getBytes(StandardCharsets.UTF_8));
        }
    }
    static JSONArray codecs() throws Exception {
        JSONArray result = new JSONArray();
        for (MediaCodecInfo info : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
            if (info.isEncoder()) continue;
            for (String type : info.getSupportedTypes()) {
                if (!type.equalsIgnoreCase(MIME)) continue;
                MediaCodecInfo.CodecCapabilities cap = info.getCapabilitiesForType(type);
                MediaCodecInfo.VideoCapabilities video = cap.getVideoCapabilities();
                JSONObject row = new JSONObject().put("name", info.getName())
                    .put("hardware", info.isHardwareAccelerated()).put("software", info.isSoftwareOnly())
                    .put("alias", info.isAlias()).put("vendor", info.isVendor())
                    .put("size_1152x1920", video.isSizeSupported(1152, 1920))
                    .put("size_rate_1152x1920_90", video.areSizeAndRateSupported(1152, 1920, 90))
                    .put("low_latency", cap.isFeatureSupported("low-latency"))
                    .put("color_formats", new JSONArray(cap.colorFormats));
                JSONArray profiles = new JSONArray();
                for (MediaCodecInfo.CodecProfileLevel p : cap.profileLevels)
                    profiles.put(new JSONObject().put("profile", p.profile).put("level", p.level));
                row.put("profiles", profiles);
                result.put(row);
            }
        }
        return result;
    }
    static JSONObject capture(Image image, File destination) throws Exception {
        if (image == null) throw new IllegalStateException("getOutputImage unavailable; raw luma unverified");
        Rect crop = image.getCropRect();
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer data = plane.getBuffer().duplicate();
        int base = data.position(), rowStride = plane.getRowStride(), pixelStride = plane.getPixelStride();
        byte[] row = new byte[crop.width()];
        try (FileOutputStream out = new FileOutputStream(destination)) {
            for (int y = crop.top; y < crop.bottom; ++y) {
                int offset = base + y * rowStride + crop.left * pixelStride;
                if (pixelStride == 1) { data.position(offset); data.get(row); }
                else for (int x = 0; x < row.length; ++x) row[x] = data.get(offset + x * pixelStride);
                out.write(row);
            }
        }
        return new JSONObject().put("file", destination.getName()).put("width", crop.width())
            .put("height", crop.height()).put("crop_left", crop.left).put("crop_top", crop.top)
            .put("row_stride", rowStride).put("pixel_stride", pixelStride).put("image_format", image.getFormat());
    }
    static JSONObject decode(String[] args) throws Exception {
        // decode INPUT.mp4 OUTPUT_DIRECTORY CODEC EXPECTED_FRAMES LOW_LATENCY CAPTURE
        String input = args[1]; File output = new File(args[2]);
        if (!output.mkdir()) throw new IllegalArgumentException("new output directory required");
        String name = args[3]; int expected = Integer.parseInt(args[4]);
        boolean lowLatency = Boolean.parseBoolean(args[5]), saveImages = Boolean.parseBoolean(args[6]);
        if (expected < 1 || expected > 900) throw new IllegalArgumentException("frames must be 1..900");
        MediaExtractor extractor = new MediaExtractor(); MediaCodec codec = null;
        JSONObject result = new JSONObject().put("schema", 1).put("status", "failed")
            .put("scope", "standalone MediaCodec byte-buffer output; no Surface/OpenXR/display")
            .put("low_latency_requested", lowLatency).put("capture_run", saveImages)
            .put("timing_semantics", "paced 90 Hz input, max 4 queued; queueInputBuffer to dequeueOutputBuffer, includes queueing and polling; capture runs additionally incur image/file work")
            .put("expected_frames", expected);
        JSONArray frames = new JSONArray(); result.put("frames", frames);
        try {
            extractor.setDataSource(input);
            int track = -1;
            for (int i = 0; i < extractor.getTrackCount(); ++i)
                if (MIME.equals(extractor.getTrackFormat(i).getString(MediaFormat.KEY_MIME))) { track = i; break; }
            if (track < 0) throw new IllegalArgumentException("no HEVC video track");
            extractor.selectTrack(track); MediaFormat format = extractor.getTrackFormat(track);
            if (format.getInteger(MediaFormat.KEY_WIDTH) != 1152 || format.getInteger(MediaFormat.KEY_HEIGHT) != 1920)
                throw new IllegalArgumentException("unexpected carrier dimensions");
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            format.setInteger(MediaFormat.KEY_LOW_LATENCY, lowLatency ? 1 : 0);
            codec = MediaCodec.createByCodecName(name);
            if (!codec.getCodecInfo().isHardwareAccelerated() || codec.getCodecInfo().isSoftwareOnly())
                throw new IllegalStateException("hardware decoder required");
            result.put("decoder", codec.getCanonicalName()).put("hardware", true).put("input_format", format.toString());
            codec.configure(format, null, null, 0); codec.start();
            result.put("configured_input_format", codec.getInputFormat().toString());
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            Map<Long, Long> queued = new HashMap<>(); Map<Long, Integer> indices = new HashMap<>();
            boolean inputEos = false, outputEos = false;
            int sent = 0, received = 0, maxPending = 0;
            long start = System.nanoTime(), deadline = start + 120000000000L;
            while (!outputEos) {
                if (System.nanoTime() > deadline) throw new IllegalStateException("120 second decoder watchdog");
                if (!inputEos && queued.size() < 4 && System.nanoTime() >= start + sent * 1000000000L / 90) {
                    int slot = codec.dequeueInputBuffer(0);
                    if (slot >= 0) {
                        ByteBuffer buffer = codec.getInputBuffer(slot);
                        if (buffer == null) throw new IllegalStateException("missing input buffer");
                        buffer.clear(); int size = extractor.readSampleData(buffer, 0);
                        if (size < 0) {
                            codec.queueInputBuffer(slot, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM); inputEos = true;
                        } else {
                            long pts = extractor.getSampleTime();
                            if (sent >= expected || queued.containsKey(pts) || indices.containsKey(pts))
                                throw new IllegalStateException("extra input or duplicate PTS");
                            indices.put(pts, sent++); queued.put(pts, System.nanoTime());
                            codec.queueInputBuffer(slot, 0, size, pts, 0); extractor.advance();
                            maxPending = Math.max(maxPending, queued.size());
                        }
                    }
                }
                int slot = codec.dequeueOutputBuffer(info, 1000);
                long ready = System.nanoTime();
                if (slot == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) result.put("output_format", codec.getOutputFormat().toString());
                if (slot >= 0) {
                    try {
                        if (info.size > 0 && (info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0) {
                            Long began = queued.remove(info.presentationTimeUs);
                            if (began == null) throw new IllegalStateException("unmatched or duplicate output PTS");
                            int frame = indices.get(info.presentationTimeUs);
                            if (frame != received) throw new IllegalStateException("unexpected frame order");
                            JSONObject row = new JSONObject().put("frame", frame).put("pts_us", info.presentationTimeUs)
                                .put("queued_ns", began).put("output_ns", ready).put("queue_to_output_ms", (ready-began)/1e6);
                            if (saveImages && (frame == 0 || frame == expected/2 || frame == expected-1)) {
                                try (Image image = codec.getOutputImage(slot)) {
                                    row.put("capture", capture(image, new File(output, "frame-"+frame+".y")));
                                }
                            }
                            frames.put(row); ++received;
                        }
                        outputEos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                    } finally { codec.releaseOutputBuffer(slot, false); }
                }
            }
            result.put("sent_frames", sent).put("decoded_frames", received).put("max_pending_frames", maxPending)
                .put("elapsed_ms", (System.nanoTime()-start)/1e6).put("output_eos", outputEos);
            if (sent != expected || received != expected || !queued.isEmpty()) throw new IllegalStateException("frame count mismatch");
            result.put("status", "pass");
            return result;
        } catch (Exception error) { result.put("error", error.toString()); throw error; }
        finally {
            try { if (codec != null) codec.release(); }
            finally { extractor.release(); write(new File(output, "result.json").toString(), result); }
        }
    }
    public static void main(String[] args) throws Exception {
        if (args.length == 2 && args[0].equals("probe"))
            write(args[1], new JSONObject().put("schema", 1).put("codecs", codecs()));
        else if (args.length == 7 && args[0].equals("decode")) decode(args);
        else throw new IllegalArgumentException("probe OUTPUT.json | decode INPUT.mp4 OUTPUT_DIR CODEC FRAMES LOW_LATENCY CAPTURE");
    }
}
