#!/usr/bin/env python3
"""Validate copies of a user's profiles without starting services or hardware."""
import hashlib, json, os, shutil, subprocess, tempfile, tomllib
from pathlib import Path
root=Path(__file__).resolve().parents[2]
source=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
with tempfile.TemporaryDirectory(prefix='wivrn-profile-check-') as tmp:
    home=Path(tmp)
    sources={}
    for name in ('wivrn/config.json','wivrn/known_keys.json','wivrn/cookie','shora-rust/server.toml'):
        path=source/name
        if path.exists():
            sources[path]=hashlib.sha256(path.read_bytes()).hexdigest()
            dest=home/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,dest)
    subprocess.run([str(root/'build/native-tests/config-migration-test'),str(home)],check=True)
    for name in ('config.json','known_keys.json'):
        original=home/'wivrn'/name; imported=home/'wivrn-pro'/name
        if original.exists():
            assert json.loads(original.read_text())==json.loads(imported.read_text()), f'{name}: inspect intentional compatibility changes'
    profile=home/'wivrn-pro/shora.toml'
    if profile.exists():
        assert profile.read_bytes()==(home/'shora-rust/server.toml').read_bytes()
        env=dict(os.environ,CARGO_TARGET_DIR=str(root/'build/shora-tests'),WIVRN_TEST_TRACKING_PROFILE=str(profile))
        log=root/'build/current-profile-validation.log'
        with log.open('w') as output:
            result=subprocess.run(['cargo','test','--offline','--locked','--manifest-path',str(root/'server/shora/Cargo.toml'),'-p','wivrn-tracking',
                '--features','face-xr','fork_worker_keeps_profile_settings_with_private_socket_overrides'],env=env,stdout=output,stderr=subprocess.STDOUT)
        assert result.returncode==0, f'Rust profile validation failed; see {log}'
        print('PASS current Shora profile parsed by integrated Rust backend; tuning, serial, face/OSC, assignments retained')
    # Dashboard must round-trip the existing JSON without adding defaults or simplifying encoders.
    os.environ['QT_QPA_PLATFORM']='offscreen'
    import sys
    sys.path.insert(0,str(root/'fork/tests'))
    from test_dashboard import FakeServer
    from wivrn_dashboard import StreamingPanel
    from PySide6.QtWidgets import QApplication
    app=QApplication([]); server=FakeServer(); panel=StreamingPanel(server)
    config=home/'wivrn-pro/config.json'
    if config.exists():
        original=json.loads(config.read_text());server.state.emit({'JsonConfiguration':config.read_text()})
        assert panel.configuration()==original
        print('PASS current WiVRn JSON imports and dashboard round-trip with all values preserved')
    panel.close()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest()==digest for path,digest in sources.items())
    print('PASS original user files unchanged; temporary copies removed on exit')
