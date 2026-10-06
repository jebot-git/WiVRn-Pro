# Private Quest distribution

Meta app: `3443645619141564`. Release channel: `ALPHA`.

Build a non-debuggable APK with an externally stored release key:

```sh
export WIVRN_BUILD_TASK=assembleRelease
export WIVRN_SIGNING_STORE=/private/path/release.p12
export WIVRN_SIGNING_ALIAS=wivrn-pro
# Set WIVRN_SIGNING_PASSWORD securely in the environment, not in Git or shell history.
fork/build-client.sh -Pwivrn_versionCode=2026100602
```

The key must be backed up and reused for every update to this package. Keep all
credentials, signing material and OVR configuration files outside the checkout.
`ovr-platform-util upload-quest-build` accepts app ID, either app secret or
user token, APK path, channel and release notes. Use a private wrapper that reads
credentials from a mode-0600 file; never put their values in shell history. Check channel assignment and
postprocessing after upload. Store installation requires the headset account to
have ALPHA access. Do not replace a release-signing key to solve an update error.

## Meta icon

`images/wivrn-pro-meta-icon.png` is the opaque 1024×1024 store icon: a white VR
headset with a cyan speech bubble on navy. It was generated for this project.
Pass `-Pwivrn_meta_icon` to the client build to package it into all Android
launcher icon densities. The default non-Meta build retains the existing icon.
The same PNG is ready for the Meta dashboard's store-icon upload.
