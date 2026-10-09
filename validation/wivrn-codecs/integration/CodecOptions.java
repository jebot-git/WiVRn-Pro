import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaCodecList;
import org.json.JSONArray;
import org.json.JSONObject;
/** Read-only capability enumeration; never sets a codec/vendor/device option. */
public final class CodecOptions {
 public static void main(String[] args) throws Exception {
  JSONArray results = new JSONArray();
  for (MediaCodecInfo info : new MediaCodecList(MediaCodecList.ALL_CODECS).getCodecInfos()) {
   if (info.isEncoder() || !info.isHardwareAccelerated()) continue;
   boolean hevc = false; for (String type : info.getSupportedTypes()) if (type.equals("video/hevc")) hevc = true;
   if (!hevc) continue;
   JSONObject item = new JSONObject().put("name", info.getName());
   MediaCodec codec = null;
   try {
    codec = MediaCodec.createByCodecName(info.getName());
    JSONArray parameters = new JSONArray();
    for (String name : codec.getSupportedVendorParameters()) {
     MediaCodec.ParameterDescriptor descriptor = codec.getParameterDescriptor(name);
     parameters.put(new JSONObject().put("name", name).put("type", descriptor == null ? -1 : descriptor.getType()));
    }
    item.put("vendor_parameters", parameters);
    item.put("standard_low_latency", info.getCapabilitiesForType("video/hevc").isFeatureSupported(MediaCodecInfo.CodecCapabilities.FEATURE_LowLatency));
   } catch (Exception e) { item.put("error", e.toString()); }
   finally { if (codec != null) codec.release(); }
   results.put(item);
  }
  System.out.println(new JSONObject().put("schema", 1).put("read_only", true).put("decoders", results).toString(2));
 }
}
