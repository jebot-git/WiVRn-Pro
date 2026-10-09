// SPDX-License-Identifier: MIT
// Standalone clock capability/calibration query. No queue work or root access.
#include <vulkan/vulkan.h>
#include <algorithm>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static std::string quote(const std::string &s) {
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else out << c;
    }
    out << '"'; return out.str();
}
static void check(VkResult r, const char *op) {
    if (r != VK_SUCCESS) throw std::runtime_error(std::string(op) + ": VkResult=" + std::to_string(r));
}
template<class T, class F> static std::vector<T> enumerate(F f, const char *op) {
    for (int attempt=0; attempt<8; ++attempt) {
        uint32_t n=0; check(f(&n, nullptr), op); std::vector<T> v(n);
        if (!n) return v;
        VkResult r=f(&n, v.data());
        if (r==VK_INCOMPLETE) continue;
        check(r, op); v.resize(n); return v;
    }
    throw std::runtime_error(std::string(op) + ": enumeration remained incomplete");
}
static std::string domain_name(VkTimeDomainEXT domain, bool khr) {
    std::string suffix=khr ? "_KHR" : "_EXT";
    switch(domain) {
        case VK_TIME_DOMAIN_DEVICE_EXT: return "VK_TIME_DOMAIN_DEVICE"+suffix;
        case VK_TIME_DOMAIN_CLOCK_MONOTONIC_EXT: return "VK_TIME_DOMAIN_CLOCK_MONOTONIC"+suffix;
        case VK_TIME_DOMAIN_CLOCK_MONOTONIC_RAW_EXT: return "VK_TIME_DOMAIN_CLOCK_MONOTONIC_RAW"+suffix;
        case VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_EXT: return "VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER"+suffix;
        default: return "UNRECOGNIZED_DOMAIN_"+std::to_string(int(domain));
    }
}
struct LogicalDevice {
    VkDevice handle=VK_NULL_HANDLE;
    ~LogicalDevice() { if(handle) vkDestroyDevice(handle, nullptr); }
};
static std::string probe(VkInstance instance, VkPhysicalDevice physical, uint32_t index) {
    VkPhysicalDeviceProperties p{}; vkGetPhysicalDeviceProperties(physical, &p);
    auto extensions=enumerate<VkExtensionProperties>([&](auto n, auto v) {return vkEnumerateDeviceExtensionProperties(physical,nullptr,n,v);}, "enumerate extensions");
    auto has=[&](const char *name) {return std::any_of(extensions.begin(),extensions.end(),[&](const auto &e) {return std::string(e.extensionName)==name;});};
    bool khr=has("VK_KHR_calibrated_timestamps"), ext=has("VK_EXT_calibrated_timestamps");
    uint32_t nq=0; vkGetPhysicalDeviceQueueFamilyProperties(physical,&nq,nullptr);
    std::vector<VkQueueFamilyProperties> queues(nq); vkGetPhysicalDeviceQueueFamilyProperties(physical,&nq,queues.data()); queues.resize(nq);
    std::ostringstream out; out << std::setprecision(10) << "{\"index\":" << index << ",\"name\":" << quote(p.deviceName)
        << ",\"vendor_id\":" << p.vendorID << ",\"device_id\":" << p.deviceID << ",\"api_version\":" << p.apiVersion
        << ",\"driver_version\":" << p.driverVersion << ",\"timestamp_period_ns_per_tick\":" << p.limits.timestampPeriod
        << ",\"timestamp_compute_and_graphics\":" << (p.limits.timestampComputeAndGraphics ? "true":"false")
        << ",\"extension_khr_available\":" << (khr?"true":"false") << ",\"extension_ext_available\":" << (ext?"true":"false") << ",\"queue_families\":[";
    for (uint32_t i=0;i<nq;++i) {
        if(i) out << ',';
        out << "{\"index\":"<<i<<",\"queue_count\":"<<queues[i].queueCount<<",\"flags\":"<<queues[i].queueFlags<<",\"timestamp_valid_bits\":"<<queues[i].timestampValidBits<<'}';
    }
    out << "],\"calibration\":";
    if (!khr && !ext) return out.str()+"{\"status\":\"unsupported_extension\",\"samples\":[]}}";
    if (p.apiVersion < VK_API_VERSION_1_1) return out.str()+"{\"status\":\"unsupported_device_vulkan_version_below_1_1\",\"samples\":[]}}";
    try {
        // KHR and EXT entry points and structures are specification-defined aliases.
        auto get_domains=reinterpret_cast<PFN_vkGetPhysicalDeviceCalibrateableTimeDomainsEXT>(vkGetInstanceProcAddr(instance,khr?"vkGetPhysicalDeviceCalibrateableTimeDomainsKHR":"vkGetPhysicalDeviceCalibrateableTimeDomainsEXT"));
        if(!get_domains) throw std::runtime_error("advertised calibration extension has no domain entry point");
        auto domains=enumerate<VkTimeDomainEXT>([&](auto n, auto v) {return get_domains(physical,n,v);},"enumerate time domains");
        std::ostringstream calibration;
        calibration << "{\"extension_selected\":" << quote(khr?"VK_KHR_calibrated_timestamps":"VK_EXT_calibrated_timestamps") << ",\"available_domains\":[";
        for(size_t i=0;i<domains.size();++i) { if(i) calibration << ','; calibration << "{\"id\":" << int(domains[i]) << ",\"name\":" << quote(domain_name(domains[i],khr)) << '}'; }
        calibration << ']';
        auto supported=[&](VkTimeDomainEXT d) {return std::find(domains.begin(),domains.end(),d)!=domains.end();};
        if(!supported(VK_TIME_DOMAIN_DEVICE_EXT) || !supported(VK_TIME_DOMAIN_CLOCK_MONOTONIC_EXT))
            return out.str()+calibration.str()+",\"status\":\"device_monotonic_pair_unavailable\",\"samples\":[]}}";
        uint32_t family=0; while(family<nq && !queues[family].queueCount) ++family;
        if(family==nq) throw std::runtime_error("no queue available for logical device");
        float priority=0.0f;
        VkDeviceQueueCreateInfo queue{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO}; queue.queueFamilyIndex=family; queue.queueCount=1; queue.pQueuePriorities=&priority;
        const char *extension=khr?"VK_KHR_calibrated_timestamps":"VK_EXT_calibrated_timestamps";
        VkDeviceCreateInfo create{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO}; create.queueCreateInfoCount=1; create.pQueueCreateInfos=&queue; create.enabledExtensionCount=1; create.ppEnabledExtensionNames=&extension;
        // No optional features requested; calibrated timestamps has no feature struct.
        LogicalDevice device; check(vkCreateDevice(physical,&create,nullptr,&device.handle),"create logical device");
        auto get_timestamps=reinterpret_cast<PFN_vkGetCalibratedTimestampsEXT>(vkGetDeviceProcAddr(device.handle,khr?"vkGetCalibratedTimestampsKHR":"vkGetCalibratedTimestampsEXT"));
        if(!get_timestamps) throw std::runtime_error("enabled calibration extension has no timestamp entry point");
        VkCalibratedTimestampInfoEXT infos[2]={{VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_EXT,nullptr,VK_TIME_DOMAIN_DEVICE_EXT},{VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_EXT,nullptr,VK_TIME_DOMAIN_CLOCK_MONOTONIC_EXT}};
        calibration << ",\"status\":\"ok\",\"logical_device_queue_family\":" << family
            << ",\"device_domain\":" << quote(domain_name(infos[0].timeDomain,khr)) << ",\"host_domain\":" << quote(domain_name(infos[1].timeDomain,khr))
            << ",\"device_timestamp_unit\":\"device_ticks\",\"host_timestamp_unit\":\"nanoseconds\",\"max_deviation_unit\":\"nanoseconds\",\"integer_encoding\":\"decimal_strings_to_preserve_uint64\",\"samples\":[";
        for(unsigned i=0;i<32;++i) {
            uint64_t timestamps[2]{}, deviation=0;
            check(get_timestamps(device.handle,2,infos,timestamps,&deviation),"get calibrated timestamps");
            if(i) calibration << ',';
            calibration << "{\"index\":" << i << ",\"device_timestamp_raw\":\"" << timestamps[0] << "\",\"clock_monotonic_timestamp_raw\":\"" << timestamps[1] << "\",\"max_deviation_ns\":\"" << deviation << "\"}";
        }
        calibration << "]}"; out << calibration.str();
    } catch (const std::exception &e) {out << "{\"status\":\"error\",\"message\":" << quote(e.what()) << ",\"samples\":[]}";}
    out << '}'; return out.str();
}
int main() {
    VkInstance instance=VK_NULL_HANDLE;
    try {
        VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.pApplicationName="standalone-clock-probe"; app.apiVersion=VK_API_VERSION_1_1;
        VkInstanceCreateInfo create{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; create.pApplicationInfo=&app;
        check(vkCreateInstance(&create,nullptr,&instance),"create Vulkan 1.1 instance");
        auto devices=enumerate<VkPhysicalDevice>([&](auto n,auto v) {return vkEnumeratePhysicalDevices(instance,n,v);},"enumerate physical devices");
        std::ostringstream out; out << "{\"schema_version\":1,\"status\":\"ok\",\"scope\":\"Clock capability and calibration only; no queue submission, rendering, OpenXR, latency measurement, or root required\",\"requested_samples_per_device\":32,\"devices\":[";
        for(uint32_t i=0;i<devices.size();++i) {if(i) out << ','; out << probe(instance,devices[i],i);}
        out << "]}"; vkDestroyInstance(instance,nullptr); instance=VK_NULL_HANDLE;
        std::cout << out.str() << '\n'; return 0;
    } catch (const std::exception &e) {
        if(instance) vkDestroyInstance(instance,nullptr);
        std::cout << "{\"schema_version\":1,\"status\":\"error\",\"message\":" << quote(e.what()) << ",\"devices\":[]}\n"; return 1;
    }
}
