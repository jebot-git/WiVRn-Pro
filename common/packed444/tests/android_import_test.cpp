// SPDX-License-Identifier: GPL-3.0-or-later
// Explicit hardware integration test; CPU readback is confined to validation.
#include "packed444/unpacker.h"
#include "vk/vk_allocator.h"
#include "vk/allocation.h"
#include <android/hardware_buffer.h>
#include <media/NdkImage.h>
#include <media/NdkImageReader.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

int main(int argc,char**argv)try{
    if(argc!=3)throw std::runtime_error("expected packed H264 and decoded-reference YUV444 paths");
    std::ifstream file(argv[1],std::ios::binary);std::vector<uint8_t> bytes{std::istreambuf_iterator<char>(file),{}};
    if(bytes.empty())throw std::runtime_error("empty sample");
    AImageReader* reader{};
    if(AImageReader_newWithUsage(512,256,AIMAGE_FORMAT_PRIVATE,AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE,4,&reader)!=AMEDIA_OK)throw std::runtime_error("reader");
    ANativeWindow* window{};AImageReader_getWindow(reader,&window);
    auto* codec=AMediaCodec_createCodecByName("c2.qti.avc.decoder");auto* format=AMediaFormat_new();
    AMediaFormat_setString(format,AMEDIAFORMAT_KEY_MIME,"video/avc");AMediaFormat_setInt32(format,"width",512);AMediaFormat_setInt32(format,"height",256);
    if(AMediaCodec_configure(codec,format,window,nullptr,0)!=AMEDIA_OK)throw std::runtime_error("configure");
    if(AMediaCodec_start(codec)!=AMEDIA_OK)throw std::runtime_error("start");
    auto idx=AMediaCodec_dequeueInputBuffer(codec,1000000);size_t capacity{};auto* buf=AMediaCodec_getInputBuffer(codec,idx,&capacity);
    if(idx<0||!buf||capacity<bytes.size())throw std::runtime_error("input");memcpy(buf,bytes.data(),bytes.size());AMediaCodec_queueInputBuffer(codec,idx,0,bytes.size(),0,0);
    idx=AMediaCodec_dequeueInputBuffer(codec,500000);if(idx>=0)AMediaCodec_queueInputBuffer(codec,idx,0,0,1,AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
    AImage* aimage{};bool rendered=false;
    for(unsigned i=0;i<100&&!aimage;++i){
        if(!rendered){AMediaCodecBufferInfo bi{};auto out=AMediaCodec_dequeueOutputBuffer(codec,&bi,20000);if(out>=0){AMediaCodec_releaseOutputBuffer(codec,out,true);rendered=true;}}
        if(rendered){AImageReader_acquireNextImage(reader,&aimage);if(!aimage)std::this_thread::sleep_for(std::chrono::milliseconds(10));}
    }
    if(!aimage)throw std::runtime_error("no output image");
    AImageCropRect crop{};
    if(AImage_getCropRect(aimage,&crop)!=AMEDIA_OK)throw std::runtime_error("crop query");
    std::cout<<"CROP "<<crop.left<<","<<crop.top<<","<<crop.right<<","<<crop.bottom<<std::endl;
    if(crop.left!=0||crop.top!=0||crop.right!=512||crop.bottom!=256)throw std::runtime_error("unexpected coded crop");
    AHardwareBuffer* ahb{};AImage_getHardwareBuffer(aimage,&ahb);AHardwareBuffer_Desc desc{};AHardwareBuffer_describe(ahb,&desc);
    std::cout<<"AHB "<<desc.width<<"x"<<desc.height<<" fmt="<<desc.format<<" usage="<<desc.usage<<std::endl;
    {
    vk::raii::Context context;vk::ApplicationInfo app{.pApplicationName="packed444 GPU AHB probe",.apiVersion=VK_API_VERSION_1_1};
    vk::raii::Instance instance(context,{.pApplicationInfo=&app});auto devices=instance.enumeratePhysicalDevices();auto& physical=devices.at(0);
    auto props=physical.getQueueFamilyProperties();uint32_t family=0;while(!(props.at(family).queueFlags&vk::QueueFlagBits::eCompute))family++;
    float priority=1;vk::DeviceQueueCreateInfo qi{.queueFamilyIndex=family,.queueCount=1,.pQueuePriorities=&priority};
    vk::PhysicalDeviceSamplerYcbcrConversionFeatures yf{.samplerYcbcrConversion=true};
    const char* exts[]={VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,VK_EXT_QUEUE_FAMILY_FOREIGN_EXTENSION_NAME};
    vk::raii::Device device(physical,{.pNext=&yf,.queueCreateInfoCount=1,.pQueueCreateInfos=&qi,.enabledExtensionCount=2,.ppEnabledExtensionNames=exts});
    auto queue=device.getQueue(family,0);vk_allocator allocator({.physicalDevice=*physical,.device=*device,.instance=*instance,.vulkanApiVersion=VK_API_VERSION_1_1},false);
    auto [memory_props,fp]=device.getAndroidHardwareBufferPropertiesANDROID<vk::AndroidHardwareBufferPropertiesANDROID,vk::AndroidHardwareBufferFormatPropertiesANDROID>(*ahb);
    std::cout<<"AHB external="<<fp.externalFormat<<" vkformat="<<vk::to_string(fp.format)<<" flags="<<vk::to_string(fp.formatFeatures)<<" suggested="<<vk::to_string(fp.suggestedYcbcrModel)<<std::endl;
    vk::StructureChain conversion_info{vk::SamplerYcbcrConversionCreateInfo{.format=vk::Format::eUndefined,.ycbcrModel=vk::SamplerYcbcrModelConversion::eRgbIdentity,.ycbcrRange=vk::SamplerYcbcrRange::eItuFull,.components=fp.samplerYcbcrConversionComponents,.xChromaOffset=fp.suggestedXChromaOffset,.yChromaOffset=fp.suggestedYChromaOffset,.chromaFilter=vk::Filter::eNearest},vk::ExternalFormatANDROID{.externalFormat=fp.externalFormat}};
    auto conversion=device.createSamplerYcbcrConversion(conversion_info.get());
    vk::StructureChain sampler_info{vk::SamplerCreateInfo{.magFilter=vk::Filter::eNearest,.minFilter=vk::Filter::eNearest,.mipmapMode=vk::SamplerMipmapMode::eNearest,.addressModeU=vk::SamplerAddressMode::eClampToEdge,.addressModeV=vk::SamplerAddressMode::eClampToEdge,.addressModeW=vk::SamplerAddressMode::eClampToEdge},vk::SamplerYcbcrConversionInfo{.conversion=*conversion}};
    auto sampler=device.createSampler(sampler_info.get());
    vk::StructureChain image_info{vk::ImageCreateInfo{.imageType=vk::ImageType::e2D,.format=vk::Format::eUndefined,.extent={desc.width,desc.height,1},.mipLevels=1,.arrayLayers=1,.samples=vk::SampleCountFlagBits::e1,.tiling=vk::ImageTiling::eOptimal,.usage=vk::ImageUsageFlagBits::eSampled,.initialLayout=vk::ImageLayout::eUndefined},vk::ExternalMemoryImageCreateInfo{.handleTypes=vk::ExternalMemoryHandleTypeFlagBits::eAndroidHardwareBufferANDROID},vk::ExternalFormatANDROID{.externalFormat=fp.externalFormat}};
    auto imported=device.createImage(image_info.get());
    vk::StructureChain mi{vk::MemoryAllocateInfo{.allocationSize=memory_props.allocationSize,.memoryTypeIndex=uint32_t(__builtin_ffs(memory_props.memoryTypeBits)-1)},vk::MemoryDedicatedAllocateInfo{.image=*imported},vk::ImportAndroidHardwareBufferInfoANDROID{.buffer=ahb}};
    auto memory=device.allocateMemory(mi.get());imported.bindMemory(*memory,0);
    vk::StructureChain vi{vk::ImageViewCreateInfo{.image=*imported,.viewType=vk::ImageViewType::e2D,.format=vk::Format::eUndefined,.subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}},vk::SamplerYcbcrConversionInfo{.conversion=*conversion}};
    auto view=device.createImageView(vi.get());
    image_allocation rgba(device,{.imageType=vk::ImageType::e2D,.format=vk::Format::eR8G8B8A8Unorm,.extent={256,256,1},.mipLevels=1,.arrayLayers=1,.usage=vk::ImageUsageFlagBits::eStorage|vk::ImageUsageFlagBits::eTransferSrc},{.usage=VMA_MEMORY_USAGE_AUTO},"packed RGBA");
    auto rgba_view=device.createImageView({.image=rgba,.viewType=vk::ImageViewType::e2D,.format=vk::Format::eR8G8B8A8Unorm,.subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}});
    buffer_allocation readback(device,{.size=256*256*4,.usage=vk::BufferUsageFlagBits::eTransferDst},{.flags=VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT,.usage=VMA_MEMORY_USAGE_AUTO,.requiredFlags=VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT},"test readback");
    wivrn::packed444::Unpacker unpacker(device,{256,256},*sampler);auto binding=unpacker.create_binding(*view,*rgba_view);
    auto pool=device.createCommandPool({.queueFamilyIndex=family});auto cmd=std::move(device.allocateCommandBuffers({.commandPool=*pool,.commandBufferCount=1})[0]);
    cmd.begin({});vk::ImageMemoryBarrier ib{.dstAccessMask=vk::AccessFlagBits::eShaderRead,.oldLayout=vk::ImageLayout::eUndefined,.newLayout=vk::ImageLayout::eGeneral,.srcQueueFamilyIndex=VK_QUEUE_FAMILY_FOREIGN_EXT,.dstQueueFamilyIndex=family,.image=*imported,.subresourceRange={.aspectMask=vk::ImageAspectFlagBits::eColor,.levelCount=1,.layerCount=1}};
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe,vk::PipelineStageFlagBits::eComputeShader,{}, {},{},ib);
    ib.dstAccessMask=vk::AccessFlagBits::eShaderWrite;ib.srcQueueFamilyIndex=ib.dstQueueFamilyIndex=VK_QUEUE_FAMILY_IGNORED;ib.image=static_cast<vk::Image>(rgba);
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe,vk::PipelineStageFlagBits::eComputeShader,{}, {},{},ib);
    unpacker.record(cmd,binding,{desc.width,desc.height});
    vk::MemoryBarrier mb{.srcAccessMask=vk::AccessFlagBits::eShaderWrite,.dstAccessMask=vk::AccessFlagBits::eTransferRead};cmd.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader,vk::PipelineStageFlagBits::eTransfer,{},mb,{},{});
    cmd.copyImageToBuffer(rgba,vk::ImageLayout::eGeneral,readback,vk::BufferImageCopy{.imageSubresource={.aspectMask=vk::ImageAspectFlagBits::eColor,.layerCount=1},.imageExtent={256,256,1}});
    mb.srcAccessMask=vk::AccessFlagBits::eTransferWrite;mb.dstAccessMask=vk::AccessFlagBits::eHostRead;cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer,vk::PipelineStageFlagBits::eHost,{},mb,{},{});
    ib.srcAccessMask=vk::AccessFlagBits::eShaderRead;ib.dstAccessMask={};ib.oldLayout=ib.newLayout=vk::ImageLayout::eGeneral;ib.srcQueueFamilyIndex=family;ib.dstQueueFamilyIndex=VK_QUEUE_FAMILY_FOREIGN_EXT;ib.image=*imported;
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader,vk::PipelineStageFlagBits::eBottomOfPipe,{}, {},{},ib);
    cmd.end();auto fence=device.createFence({});queue.submit(vk::SubmitInfo{.commandBufferCount=1,.pCommandBuffers=&*cmd},*fence);if(device.waitForFences(*fence,true,5'000'000'000)!=vk::Result::eSuccess)throw std::runtime_error("GPU timeout");
    vmaInvalidateAllocation(allocator,readback,0,VK_WHOLE_SIZE);
    std::ifstream reference_file(argv[2],std::ios::binary);
    std::vector<uint8_t> reference{std::istreambuf_iterator<char>(reference_file),{}};
    if(reference.size()!=256*256*3)throw std::runtime_error("reference must contain 256x256 planar YUV444");
    const auto* rgba_pixels=static_cast<const uint8_t*>(readback.map());
    unsigned maximum_error=0;
    for(size_t i=0;i<256*256;i++){
        float y=reference[i]/255.f,cb=reference[65536+i]/255.f-.5f,cr=reference[131072+i]/255.f-.5f;
        float expected[3]={y+1.5748f*cr,y-.187324f*cb-.468124f*cr,y+1.8556f*cb};
        for(unsigned c=0;c<3;c++)maximum_error=std::max(maximum_error,unsigned(std::abs(int(std::lround(std::clamp(expected[c],0.f,1.f)*255))-int(rgba_pixels[4*i+c]))));
        if(rgba_pixels[4*i+3]!=255)throw std::runtime_error("alpha is not opaque");
    }
    std::cout<<"GPU packed444 import/unpack maximum RGB error "<<maximum_error<<"/255"<<std::endl;
    if(maximum_error>1)throw std::runtime_error("GPU unpack differs from decoded-plane reference");
    }
    AImage_delete(aimage);AMediaCodec_stop(codec);AMediaCodec_delete(codec);AMediaFormat_delete(format);AImageReader_delete(reader);
    return 0;
}catch(const std::exception&e){std::cerr<<e.what()<<std::endl;return 1;}
