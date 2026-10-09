// SPDX-License-Identifier: GPL-3.0-or-later
package org.meumeu.wivrn;

import android.graphics.Rect;
import android.media.Image;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaFormat;
import java.nio.ByteBuffer;

/** Raw Image output used only by the codec-test native build. No RGB conversion. */
public final class CodecTestDecoder {
    private final MediaCodec codec;
    private final int width, height;
    public CodecTestDecoder(int width, int height) throws Exception {
        this.width=width; this.height=height;
        codec=MediaCodec.createByCodecName("c2.qti.hevc.decoder");
        try {
            MediaFormat format=MediaFormat.createVideoFormat("video/hevc",width,height*3);
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE,16*1024*1024);
            codec.configure(format,null,null,0);codec.start();
        } catch(Exception e) {codec.release();throw e;}
    }
    public byte[] decode(byte[] bytes,long frame) throws Exception {
        int input=codec.dequeueInputBuffer(1000000);
        if(input<0)throw new IllegalStateException("carrier input timeout");
        ByteBuffer buffer=codec.getInputBuffer(input);
        if(buffer==null||buffer.capacity()<bytes.length)throw new IllegalStateException("carrier input capacity");
        buffer.clear();buffer.put(bytes);codec.queueInputBuffer(input,0,bytes.length,frame,0);
        MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();long end=System.nanoTime()+2000000000L;
        while(System.nanoTime()<end){
            int output=codec.dequeueOutputBuffer(info,10000);
            if(output<0)continue;
            try {
                if(info.presentationTimeUs!=frame)throw new IllegalStateException("carrier PTS mismatch");
                try(Image image=codec.getOutputImage(output)){
                    if(image==null)throw new IllegalStateException("raw carrier image missing");
                    Rect crop=image.getCropRect();
                    if(crop.width()!=width||crop.height()!=height*3)throw new IllegalStateException("carrier crop mismatch");
                    Image.Plane plane=image.getPlanes()[0];ByteBuffer data=plane.getBuffer().duplicate();
                    int base=data.position(),stride=plane.getRowStride(),pixel=plane.getPixelStride();
                    byte[] rgba=new byte[width*height*4];
                    for(int y=0;y<height;y++)for(int x=0;x<width;x++){
                        int target=4*(y*width+x);rgba[target+3]=(byte)255;
                        for(int c=0;c<3;c++)rgba[target+c]=data.get(base+(crop.top+c*height+y)*stride+(crop.left+x)*pixel);
                    }
                    return rgba;
                }
            } finally {codec.releaseOutputBuffer(output,false);}
        }
        throw new IllegalStateException("carrier output timeout");
    }
    public void close(){codec.release();}
}
