// SPDX-License-Identifier: GPL-3.0-or-later
// Read-only capability test: creates temporary NV12/P010 VA surfaces, exports
// their actual DRM modifiers and asks Vulkan about importable storage images.
#include <cstdio>
#include <cstdlib>
#include <drm_fourcc.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <va/va.h>
#include <va/va_drm.h>
#include <va/va_drmcommon.h>
#include <vector>
#include <vulkan/vulkan.h>
static void va_check(VAStatus s)
{
	if (s != VA_STATUS_SUCCESS)
	{
		fprintf(stderr, "VA: %s\n", vaErrorStr(s));
		exit(1);
	}
}
int main(int argc, char ** argv)
{
	const char * node = argc > 1 ? argv[1] : "/dev/dri/renderD129";
	int fd = open(node, O_RDWR | O_CLOEXEC);
	if (fd < 0)
	{
		perror(node);
		return 1;
	}
	struct stat st{};
	fstat(fd, &st);
	auto display = vaGetDisplayDRM(fd);
	int majorv, minorv;
	va_check(vaInitialize(display, &majorv, &minorv));
	VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
	app.apiVersion = VK_API_VERSION_1_2;
	VkInstanceCreateInfo ci{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
	ci.pApplicationInfo = &app;
	VkInstance instance;
	if (vkCreateInstance(&ci, nullptr, &instance) != VK_SUCCESS)
		return 1;
	uint32_t count = 0;
	vkEnumeratePhysicalDevices(instance, &count, nullptr);
	std::vector<VkPhysicalDevice> devices(count);
	vkEnumeratePhysicalDevices(instance, &count, devices.data());
	VkPhysicalDevice selected = VK_NULL_HANDLE;
	for (auto device: devices)
	{
		VkPhysicalDeviceDrmPropertiesEXT drm{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRM_PROPERTIES_EXT};
		VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, &drm};
		vkGetPhysicalDeviceProperties2(device, &p);
		if (drm.hasRender && drm.renderMajor == major(st.st_rdev) && drm.renderMinor == minor(st.st_rdev))
		{
			selected = device;
			printf("GPU: %s, %s\n", p.properties.deviceName, node);
			break;
		}
	}
	if (!selected)
	{
		fprintf(stderr, "No matching Vulkan device\n");
		return 1;
	}
	bool all = true;
	for (bool ten: {false, true})
	{
		VASurfaceAttrib attr{};
		attr.type = VASurfaceAttribPixelFormat;
		attr.flags = VA_SURFACE_ATTRIB_SETTABLE;
		attr.value.type = VAGenericValueTypeInteger;
		attr.value.value.i = ten ? VA_FOURCC_P010 : VA_FOURCC_NV12;
		VASurfaceID surface;
		va_check(vaCreateSurfaces(display, ten ? VA_RT_FORMAT_YUV420_10 : VA_RT_FORMAT_YUV420, 576, 640, &surface, 1, &attr, 1));
		VADRMPRIMESurfaceDescriptor desc{};
		va_check(vaExportSurfaceHandle(display, surface, VA_SURFACE_ATTRIB_MEM_TYPE_DRM_PRIME_2, VA_EXPORT_SURFACE_READ_WRITE | VA_EXPORT_SURFACE_SEPARATE_LAYERS, &desc));
		for (uint32_t i = 0; i < desc.num_layers; ++i)
		{
			auto modifier = desc.objects[desc.layers[i].object_index[0]].drm_format_modifier;
			VkFormat format = i == 0 ? (ten ? VK_FORMAT_R10X6_UNORM_PACK16 : VK_FORMAT_R8_UNORM) : (ten ? VK_FORMAT_R10X6G10X6_UNORM_2PACK16 : VK_FORMAT_R8G8_UNORM);
			VkPhysicalDeviceExternalImageFormatInfo ext{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO};
			ext.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
			VkPhysicalDeviceImageDrmFormatModifierInfoEXT mod{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT, &ext};
			mod.drmFormatModifier = modifier;
			mod.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
			VkPhysicalDeviceImageFormatInfo2 info{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2, &mod};
			info.format = format;
			info.type = VK_IMAGE_TYPE_2D;
			info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
			info.usage = VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
			VkExternalImageFormatProperties external{VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES};
			VkImageFormatProperties2 props{VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2, &external};
			auto result = vkGetPhysicalDeviceImageFormatProperties2(selected, &info, &props);
			bool ok = result == VK_SUCCESS && (external.externalMemoryProperties.externalMemoryFeatures & VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT);
			printf("%s plane=%u modifier=0x%llx format=%d storage+import=%s (VkResult %d)\n", ten ? "P010" : "NV12", i, (unsigned long long)modifier, format, ok ? "YES" : "NO", result);
			all &= ok;
		}
		for (uint32_t i = 0; i < desc.num_objects; ++i)
			close(desc.objects[i].fd);
		va_check(vaDestroySurfaces(display, &surface, 1));
	}
	vkDestroyInstance(instance, nullptr);
	vaTerminate(display);
	close(fd);
	puts("Capability probe only: synchronization, write correctness and codec roundtrip still require validation.");
	return all ? 0 : 2;
}
