#include <vulkan/vulkan.h>
#include <cstdio>
#include <vector>
int main(){
 VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};app.pApplicationName="codec-transfer-probe";app.apiVersion=VK_API_VERSION_1_1;
 VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};info.pApplicationInfo=&app;
 VkInstance instance; if(vkCreateInstance(&info,nullptr,&instance)!=VK_SUCCESS)return 1;
 uint32_t n=0;vkEnumeratePhysicalDevices(instance,&n,nullptr);std::vector<VkPhysicalDevice> devices(n);vkEnumeratePhysicalDevices(instance,&n,devices.data());
 puts("{\"schema\":1,\"read_only\":true,\"devices\":[");
 for(unsigned i=0;i<n;i++){
  if(i)puts(",");VkPhysicalDeviceProperties p;VkPhysicalDeviceFeatures f;
  vkGetPhysicalDeviceProperties(devices[i],&p);vkGetPhysicalDeviceFeatures(devices[i],&f);
  printf("{\"name\":\"%s\",\"vendor_id\":%u,\"device_id\":%u,\"astc_ldr\":%s,\"etc2\":%s,\"bc\":%s,\"formats\":[",p.deviceName,p.vendorID,p.deviceID,f.textureCompressionASTC_LDR?"true":"false",f.textureCompressionETC2?"true":"false",f.textureCompressionBC?"true":"false");
  struct F{VkFormat format;const char* name;};F formats[]={{VK_FORMAT_ASTC_4x4_SRGB_BLOCK,"ASTC_4x4_sRGB"},{VK_FORMAT_ASTC_6x6_SRGB_BLOCK,"ASTC_6x6_sRGB"},{VK_FORMAT_ASTC_8x8_SRGB_BLOCK,"ASTC_8x8_sRGB"},{VK_FORMAT_ETC2_R8G8B8_SRGB_BLOCK,"ETC2_RGB_sRGB"},{VK_FORMAT_BC7_SRGB_BLOCK,"BC7_sRGB"}};
  bool first=true;for(auto x:formats){VkFormatProperties fp;vkGetPhysicalDeviceFormatProperties(devices[i],x.format,&fp);VkImageFormatProperties ip{};
   auto r=vkGetPhysicalDeviceImageFormatProperties(devices[i],x.format,VK_IMAGE_TYPE_2D,VK_IMAGE_TILING_OPTIMAL,VK_IMAGE_USAGE_SAMPLED_BIT|VK_IMAGE_USAGE_TRANSFER_DST_BIT,0,&ip);
   if(!first)printf(",");first=false;
   printf("{\"name\":\"%s\",\"optimal_features\":%u,\"sampled_transfer_dst_image\":%s,\"max_width\":%u,\"max_height\":%u}",x.name,fp.optimalTilingFeatures,r==VK_SUCCESS?"true":"false",ip.maxExtent.width,ip.maxExtent.height);
  }printf("]}");
 }puts("]}");vkDestroyInstance(instance,nullptr);
}
