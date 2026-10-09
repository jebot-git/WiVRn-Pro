Read-only Vulkan foveation capability probe

Build: bash prototypes/vr444/experiments/foveation_capabilities/build.sh OUTPUT
Run probe-host or probe-android without arguments. Output is JSON; extension
features/properties are queried only when the relevant extension is advertised.
queried=0 means the zero fields are placeholders, not queried feature values.
VkBool32 fields are emitted as0/1. No VkDevice, queue, rendering, OpenXR instance,
gaze permission, tracker, foveation profile or swapchain is created.

Capability discovery cannot prove a usable complete rendering path, driver
correctness, eye-tracker data, speedup, or visible quality. Enabled feature chains,
formats, image creation, render-pass/swapchain compatibility and actual timing must
be tested separately. Fragment shading-rate features do not accelerate compute
codec shaders or recover lost host image detail.
