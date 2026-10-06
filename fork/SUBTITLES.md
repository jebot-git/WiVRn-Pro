# Speech and scene translation (experimental)

The unified WiVRn Pro dashboard has a **Subtitles** tab. Enable recognition,
choose an output language (English by default; Serbian is also preset), enter your OpenAI API key, and
select **Apply during session**. Turn off **Enable recognition and translation**
and apply to stop capture and clear all captions. Changes are reloaded every
250 ms; the VR session does not need to restart. The headset Audio settings also
have **Show live subtitles**, which only hides/shows the overlay and does not
stop recognition or API usage. Use the desktop enable switch to stop processing.

Defaults:

- Recognition and translation disabled until explicitly enabled.
- Speech recognition: `gpt-4o-transcribe-diarize`, with anonymous speaker labels.
- Translation: `gpt-6-luna`, Responses API, reasoning effort `none`, `store: false`.
- Output presets: English and Serbian; choose `Original` to skip translation.
- Three-second maximum chunks, 0.5-second pause detection, five-second display.
- Voice colours and approximate stereo direction enabled.
- Jupiteroid Regular subtitle font, with a selectable system sans-serif alternative.
  The bundled CC0 font includes Serbian Cyrillic and Latin glyphs; other scripts
  use available system-font fallbacks. See `assets/Jupiteroid-Regular.ttf.license`
  for its pinned source and `assets/Jupiteroid-LICENSE.txt` for the licence.

The model names, target language (including custom languages), display size,
duration, chunk timing, and activity threshold are configurable. Voice distinction
requires the diarization model. With distinction disabled, you can choose
`gpt-4o-mini-transcribe` or another compatible JSON transcription model.

## What spatial placement and voice colours mean

WiVRn receives a mixed stereo **speaker** stream, not game-object positions or
separate per-character audio. The extension estimates horizontal direction from
stable left/right energy differences during each diarized speech segment. It
places the caption two metres away, using the headset pose recorded at capture
time, rather than where you happen to be looking after translation finishes.
The distance, elevation and front/back position are not measured. Moving,
balanced, overlapping or otherwise ambiguous audio is shown in centered captions.

Up to four caption boxes can be displayed simultaneously. Each voice has an
anonymous label and a distinct colour among the active boxes. Diarization is
probabilistic. Short unknown turns receive provisional IDs instead of treating
chunk-local labels such as “A” as permanent identities. Up to four clear,
non-overlapping two-second voice clips are kept **only in RAM** and submitted as
references on subsequent transcription requests to help maintain speaker labels.
References and IDs reset on configuration changes and session reconnects. This
is not identification of real people. Background music, reverberation, short
utterances and overlapping voices can impair transcription or matching.

Audio activity gating is an energy threshold, not a trained speech detector.
Recognition decides what words are present; non-speech hallucinations remain
possible. This is phrase-level live captioning with chunking plus two API calls,
not a word-synchronous interpreter. Long-running inference cannot block VR:
the queue holds at most two pending audio chunks and stale results over twelve
seconds old are discarded. Consecutive turns by the same speaker in one response
are combined. Up to sixteen turns are accepted per chunk.

## OCR scene translation

The same tab has a separate **Enable scene text translation** switch, English /
Serbian / Original targets, scan interval (default five seconds after a result),
overlay duration and approximate plane distance. It shares the model, font and
API key above. Speech can remain disabled while OCR runs, and vice versa. The
headset's **Show scene translations** checkbox only hides the result; disable
scene translation in the dashboard to stop image processing and API usage.

When enabled with a key, the compositor renders a 1024×1024 left-eye view of the
streamed OpenXR layers for OCR. This is captured before client subtitles and OCR
overlays, avoiding recursive translation. It does not independently capture the desktop, headset camera, passthrough, or
headset system UI. Desktop windows displayed inside the streamed VR scene are
part of that scene and are included. A separate low-rate layer pass
and bounded GPU readback are used only on request; the normal video path retains
its fast path. Images pass to the Python worker through a sealed RAM-backed file
descriptor over private IPC, are encoded as PNG in memory, and are sent to GPT-6
Luna through the Responses image-input API. Only one frame/request is in flight;
there is no image backlog. Missing frames time out, and API errors back off.

The model reads and translates up to four clear regions and estimates each
region's original text foreground and apparent background RGB colours. The
headset draws opaque, rectangular replacement patches using those colours and
the selected font. Bounding boxes and colours are model estimates; complex
backgrounds, outlines, angled text and small lettering may not match precisely.
There is no exact depth reconstruction or font-face recognition. The overlay
uses the captured camera projection and an adjustable approximate plane (two
metres by default). Head rotation over 15 degrees, translation over 12 cm,
tracking/reference loss, expiry or disable discards the old patches. Moving
objects and animated UI are not tracked and can become misaligned before expiry.
Results more than twelve seconds after capture are discarded. Best suited to
stationary signs and menus while looking steadily at them.

Images stay in bounded memory locally. Text and recognized speech clips can be
saved with the separate opt-in logging/training controls described in
[local recognition and recordings](local-ai/README.md). In cloud mode,
**scene images are uploaded to OpenAI while this option is enabled** and account
billing/provider data handling apply. Image requests already sent cannot be
retracted. Both speech and scene translation default to disabled.

## Backend and credentials

The native server owns an independent Python worker for each connected session.
It uses `pw-record` to monitor only `wivrn.sink`, with sink-monitor capture and
fallback disabled. It never opens or changes the headset microphone. Network
requests and recognition run outside the real-time audio and rendering threads.
The private IPC channel closes when the native owner exits. Worker failures have
a bounded restart delay; API failures back off instead of replaying old speech.

Requires Python 3.11 and PipeWire's `pw-record` on the server. No OpenAI Python
SDK is required. The CMake server install includes the backend independently of
the dashboard. Build-tree runs find the source backend automatically.

Files:

- `$XDG_CONFIG_HOME/wivrn-pro/subtitles.json` (default `~/.config/...`): settings.
- `$XDG_CONFIG_HOME/wivrn-pro/subtitles-openai.key`: optional private API key,
  created with mode 0600. Alternatively, supply `OPENAI_API_KEY` in the server's
  environment. A saved key takes precedence. Never put a key in this repository.
- `$XDG_RUNTIME_DIR/wivrn-pro/subtitles-status.json`: status/heartbeat only.

Audio, voice references and recognized/translated text are not written to local
logs or files. Keys never go to the headset or its configuration. Audio and
reference clips **are sent to OpenAI when enabled**, and translations are also
sent to OpenAI; normal account billing and provider data handling apply. Disabling
or changing settings discards queued audio and late results and suppresses any
subsequent translation call from an old job. A request already sent cannot be
retracted. HTTP error bodies are not exposed in status or logs.

The new subtitle packet changes WiVRn's generated protocol hash. Deploy the
matching server and APK together; old clients will fail protocol negotiation.
Keep both builds at the same protocol revision when installing or reverting.

## Validation and remaining hardware checks

`fork/test-offline.sh` includes configuration/UI tests, mocked OpenAI request and
diarization tests, cancellation/disable tests, wire validation, source-pose
placement, colour uniqueness, expiry and worker-lifecycle tests. OCR tests cover
image request construction, bounding-box/colour validation, independent settings,
stale cancellation, sealed frame IPC, projection placement and movement hiding. Its native
overlay test needs system GLM headers (`libglm-dev`) or an existing Android GLM
cache; `GLM_INCLUDE_DIR` can select another installation.

Server and Android builds must pass. Before relying on the feature, test with
actual API access and the headset: multilingual recognition, mixed dialogue and
music, voice-colour consistency, readability, turning the head during inference,
recenter/tracking loss, disconnect/reconnect, disabling mid-request, and latency.
No live API/hardware performance is inferred from mocked tests.

Official API references:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): text
  model used for translation, not direct audio recognition.
- [Speech transcription and diarization](https://developers.openai.com/api/docs/guides/speech-to-text).
- [Responses text generation](https://developers.openai.com/api/docs/guides/text).

- [Images and vision](https://developers.openai.com/api/docs/guides/images-vision).
