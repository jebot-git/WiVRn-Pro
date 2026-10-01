#!/usr/bin/env python3
"""Launch the separate Quest spatial source without printing its local credential."""
import argparse
import json
from pathlib import Path
import subprocess
parser = argparse.ArgumentParser()
parser.add_argument('--endpoint', type=Path, required=True)
parser.add_argument('--install', action='store_true')
parser.add_argument('--calibration', action='store_true', help='Capture controller poses with guided marker capture paused')
parser.add_argument('--serial', help='ADB transport, e.g. HEADSET_IP:5555 for wireless ADB')
args = parser.parse_args()
root = Path(__file__).resolve().parent
config = json.loads(args.endpoint.read_text())
adb = ['adb'] + (['-s', args.serial] if args.serial else [])
if args.install:
    subprocess.run(adb + ['install', '--no-incremental', '-r', str(root/'build/RoomSource.apk')], check=True)
subprocess.run(adb + ['reverse','tcp:47845',f'tcp:{config["port"]}'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(adb + ['shell','am','force-stop','org.vrmcompanion.roomsource'],check=True)
subprocess.run(adb + ['shell','am','start','-n','org.vrmcompanion.roomsource/.MainActivity',
                '--ei','port','47845','--es','token',config['token'],
                '--ez','calibration',str(args.calibration).lower()], check=True,stdout=subprocess.DEVNULL)
print('Separate Quest spatial source launched; stock WiVRn packages unchanged.')
