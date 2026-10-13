WiVRn microphone A/B kit — 2026-10-07

PREPARED ONLY. Prompt the user before launching the test suite.
No test pair has been installed and no test recording has been made during preparation.

Comparison
  A: Exact upstream v26.9 Android audio.cpp/audio.h and PipeWire audio_pipewire.cpp.
     Upstream commit bbc6e4cc36c355fa6180980abd231673dc15115d.
  B: Current Pro microphone keep-alive implementation, including its watchdog,
     presence handling, stream recreation and compatibility fallback.
  This compares audio subsystems in the same Pro application, not two whole-app
  upstream/Pro builds. Audio playback is also implemented in those upstream files.
  Both retain the 2026100705 blackout fixes, video code, Pro features and settings.
  Network polling remains identical (10 ms with audio) to isolate audio behavior.
  A removes Pro-only microphone capture/restart controls. B retains them; do not
  use those controls during comparison. Common microphone settings remain shared.

Artifacts and restoration
  manifest.json records hashes, versions and the exact upstream file hashes.
  A.apk + A-server and B.apk + B-server are matched pairs. Do not mix them.
  restore.apk + restore-server are the working pair backed up during preparation.
  All APKs use package org.meumeu.wivrn.spatial, the existing signing certificate
  and versionCode 2026100705. Equal-version replacement retains application data.
  A/B version names and launcher labels identify the installed audio variant.
  The switcher verifies hashes, saves the current pair, stops the user service,
  replaces both artifacts, restores prior service activity and opens the client.
  This interrupts VR. Failed switches attempt restoration and save a receipt.
  It refuses a changed installed versionCode. Never uninstall to work around it.
  Nothing is uploaded. Build outputs do not alter the installed pair.

Commands (run from the artifact directory)
  python3 audio_test.py --serial 192.168.1.110:5555 status
  python3 audio_test.py --serial 192.168.1.110:5555 switch A
  python3 audio_test.py --serial 192.168.1.110:5555 run A1
  python3 audio_test.py --serial 192.168.1.110:5555 switch restore

Protocol, after approval
  Use order A1, B1, B2, A2. Switch to the corresponding matched pair before each
  run, including B2, for a freshly started client and server. Wait for reconnection.
  Allow roughly 25–35 minutes, including switching and physical interaction.
  If rebooting the headset to clear audio HAL state, do so before EVERY run and
  note it; do not selectively reboot only a failed arm. Keep battery/charging,
  sleep settings, room, position, network, PC workload and speaking level similar.
  Set microphone ON, Unprocessed OFF, fixed 90 Hz in both arms. Retain identical
  speaker volume and encoder settings. Do not change settings between arms.
  Close/disconnect PC microphone consumers, including GoofCord's voice input.
  Do not open a separate mic meter or monitor; it would change reader demand.
  The runner refuses readers already attached and logs readers throughout the run.
  It records only the named WiVRn microphone and disables default-device fallback.

  Each guided run uses one continuous PC reader for:
    1. Ten seconds of baseline speech.
    2. Headset set down for 15 seconds without pressing power, then return/speech.
    3. Three sleep/wake cycles: brief power-button sleep, 60 seconds set down,
       wake and wear, then ten seconds of speech after the image returns.
  Then it removes the PC reader for 30 seconds with the headset awake, starts a
  new reader and records ten seconds of speech. Keep other readers closed.
  Press Enter at the runner's prompts so physical events have timestamps.
  Do not toggle microphone, use Restart microphone or reconnect during a run.
  If recovery fails, record that observation before any recovery action.
  Ctrl+C ends recording and saves collected evidence. It does not switch variants.

Interpretation
  Review continuous.wav and after-reader-gap.wav after each run. Compare the
  marked speech after each return with baseline; note missing speech, delayed
  return, distortion and video blackouts separately. Quiet audio alone is not
  proof of a failure. One-second PCM statistics assist review, not pass/fail.
  WAV position can diverge from wall-clock event time when the source stalls.
  Check headset.log for Session state changed transitions (STOPPING/READY for a
  full session stop/restart, VISIBLE/FOCUSED for return). A power-button press or
  off-head interval alone does not prove the intended lifecycle occurred. Report
  the transitions actually observed; repeat an invalid physical cycle separately.
  Review validity.json and pipewire.jsonl. Competing readers invalidate the
  reader-demand comparison. The graph is sampled every second, so very brief
  changes can be missed. A missing source or recorder exit makes a run incomplete
  and is itself useful evidence; do not silently count it as a successful run.
  Restore the original pair after the final run or after stopping the experiment.
  Repeated A-only failure supports keep-alive improving that lifecycle; repeated
  B-only failure supports a keep-alive regression. Both failing suggests a shared
  issue. Neither failing leaves the intermittent problem unresolved.

Evidence
  runs/<timestamp>-<label>/ holds local voice recordings, events.json, start.json,
  headset.log, server.log, pipewire.jsonl, validity.json and audio-summary.json.
  The runner uses owner-only permissions for new output and does not clear logs,
  change the default microphone, close other applications or modify audio settings.
  transactions/ holds switch receipts and rollback copies. Recordings stay local.
  Build/signature/alignment/dependency checks completed during preparation are
  distinct from the still-pending offline suite and physical microphone tests.
