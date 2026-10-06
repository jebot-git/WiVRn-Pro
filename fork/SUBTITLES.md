# Subtitles moved to Spatial Subtitles

Speech recognition, voice colours, translation, OCR, recording controls, and
configuration now belong to the independent [Spatial Subtitles application](../apps/spatial-subtitles/README.md).
They no longer run in the WiVRn server, dashboard, compositor, or headset client.

Recognition uses local OpenVINO models. Translation can use local OpenVINO or
the optional DeepL API Free text service. The former OpenAI, DeepSeek,
and ChatGPT/Codex translation paths remain removed. Existing private API key
files are not imported or read. Model and dataset downloads happen only in the
explicit setup/training tools.

An unused legacy packet layout remains in WiVRn solely to keep its wire protocol
compatible with the installed ALPHA client. There is no subtitle producer, API
hook, capture hook, or renderer attached to that packet.
