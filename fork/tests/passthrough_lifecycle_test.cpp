// SPDX-License-Identifier: GPL-3.0-or-later
#include "xr/instance.h"
#include "xr/session.h"
#include <cassert>
#include <cstring>
#include <iostream>

static int created = 0, destroyed = 0, layer_created = 0, layer_destroyed = 0;
static int starts = 0, pauses = 0, resumes = 0, layer_pauses = 0;
static bool running = false, visible = false;
namespace xr { const std::error_category & error_category() { return std::generic_category(); } }
static XrResult XRAPI_CALL create(XrSession, const XrPassthroughCreateInfoFB *, XrPassthroughFB * p) {
    ++created; *p = reinterpret_cast<XrPassthroughFB>(1); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroy(XrPassthroughFB) { ++destroyed; return XR_SUCCESS; }
static XrResult XRAPI_CALL create_layer(XrSession, const XrPassthroughLayerCreateInfoFB *, XrPassthroughLayerFB *p) {
    ++layer_created; *p = reinterpret_cast<XrPassthroughLayerFB>(2); return XR_SUCCESS;
}
static XrResult XRAPI_CALL destroy_layer(XrPassthroughLayerFB) { ++layer_destroyed; return XR_SUCCESS; }
static XrResult XRAPI_CALL start(XrPassthroughFB) { assert(!running); running = true; ++starts; return XR_SUCCESS; }
static XrResult XRAPI_CALL pause_passthrough(XrPassthroughFB) { assert(running && !visible); running = false; ++pauses; return XR_SUCCESS; }
static XrResult XRAPI_CALL resume(XrPassthroughLayerFB) { assert(running && !visible); visible = true; ++resumes; return XR_SUCCESS; }
static XrResult XRAPI_CALL pause_layer(XrPassthroughLayerFB) { assert(visible); visible = false; ++layer_pauses; return XR_SUCCESS; }
extern "C" XrResult XRAPI_CALL xrDestroyInstance(XrInstance) { return XR_SUCCESS; }
extern "C" XrResult XRAPI_CALL xrDestroySession(XrSession) { return XR_SUCCESS; }
extern "C" XrResult XRAPI_CALL xrGetInstanceProcAddr(XrInstance, const char *name, PFN_xrVoidFunction *p) {
#define FN(n, f) if (!std::strcmp(name, #n)) { *p = reinterpret_cast<PFN_xrVoidFunction>(f); return XR_SUCCESS; }
    FN(xrCreatePassthroughFB, create)
    FN(xrDestroyPassthroughFB, destroy)
    FN(xrCreatePassthroughLayerFB, create_layer)
    FN(xrDestroyPassthroughLayerFB, destroy_layer)
    FN(xrPassthroughStartFB, start)
    FN(xrPassthroughPauseFB, pause_passthrough)
    FN(xrPassthroughLayerResumeFB, resume)
    FN(xrPassthroughLayerPauseFB, pause_layer)
#undef FN
    assert(false); return XR_ERROR_FUNCTION_UNSUPPORTED;
}
int main() {
    xr::instance instance;
    xr::session session;
    {
        xr::passthrough_fb passthrough(instance, session);
        auto layer = reinterpret_cast<XrCompositionLayerPassthroughFB *>(passthrough.layer())->layerHandle;
        for (int i = 0; i < 10000; ++i) {
            passthrough.start(); // Repeated visible frames must not restart an active runtime object.
            passthrough.pause(); passthrough.pause();
            passthrough.start();
            assert(reinterpret_cast<XrCompositionLayerPassthroughFB *>(passthrough.layer())->layerHandle == layer);
        }
        assert(created == 1 && layer_created == 1 && destroyed == 0 && layer_destroyed == 0);
        assert(starts == 10001 && resumes == starts && pauses == 10000 && layer_pauses == pauses);
    }
    assert(destroyed == 1 && layer_destroyed == 1);
    std::cout << "Passthrough: 10,000 pause/resume cycles reuse one layer; repeated state calls are idempotent\n";
}
