Application-owner scene retirement (codec-test build only)
=========================================================
The existing scene_stack.pop_back()/clear() could relinquish the final owner
reference while a decoder callback temporarily retained scene.lock(). A later
worker-side destructor could join its own decoder thread, or cleanup could
reach the borrowed Vulkan device before the worker released the stream.

application::pop_scene now removes the entry under scene_stack_lock and keeps
a local shared_ptr through scene::prepare_exit(). cleanup swaps the whole stack
under the same lock, calls every hook, then releases the local owners. Joins and
queue drains never occur while scene_stack_lock is held. The API is compiled
only for WIVRN_CODEC_TESTS; ordinary builds retain their previous behavior.

This application seam MUST be integrated with the compositor stream override.
That override stops network/tracking, drains the actual application queue,
retires producer identities and handles under decoder/frames locks, destroys
retired decoders outside those locks, and retains an idempotent destructor
fallback. Its stalled-render pop must release shared decoder_mutex before the
hook and return an empty XR frame afterward. Queue idle, rather than an often
reset fence, handles submission failure; device-lost is terminal, and unknown
completion errors terminate instead of freeing potentially live resources.

The owner is the serialized application render thread for pop, or cleanup after
that thread has joined. Concurrent arbitrary-thread pop/render is not a newly
supported contract. The scene base's no-op hook does not establish safety for
other scene classes with their own worker ownership. The stream override is the
required concrete implementation for this research path. Shutdown can block
waiting for workers/driver completion; external acquisition watchdogs remain
necessary. No normal-frame performance measurement or speedup is claimed.

validate.py extracts the actual cleanup retirement prefix and complete pop_scene
method directly from the overlay. A standalone callback model exercises 105
scenes under ASan/UBSan and TSan, including temporary worker strong references,
an extra render owner, multiple-scene cleanup, empty/repeated operations,
unlocked stack hooks and device-before-scene lifetime. The old exact app source
fails the same ownership assertion as a negative control. This tests the actual
method bodies but does not execute native Vulkan/OpenXR or real decoder code.

syntax.py uses a temporary Clang VFS mapping for the complete actual Android
application TU, with diagnostics enabled and disabled. It reads existing build
metadata without writing native/build trees. Both passes are source-bound in
results/android-syntax.json. scene.h was copied from immutable native checkout
639120d1c8b2b71371a3120e176efdb72e0b356d; original SHA-256:
25b7991d4b56380cfa30df64c267befd5fe6bf10035ff21012bbd82c26067df5.

Root owns combined overlay manifest/patch reconciliation, linked build and
actual-device lifecycle validation. This branch does not modify those manifests
or claim that a standalone ownership model validates the full native lifecycle.
