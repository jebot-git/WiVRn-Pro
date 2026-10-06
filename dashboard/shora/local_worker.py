# SPDX-License-Identifier: GPL-3.0-or-later
"""Private socket worker, launched with the optional OpenVINO interpreter."""
import argparse
import base64
import json
import socket
import struct
import sys
from local_recognition import LocalRecognizer

MAX_REQUEST = 8 * 1024 * 1024


def read_exact(channel, count):
    data = bytearray()
    while len(data) < count:
        part = channel.recv(count-len(data))
        if not part: raise EOFError()
        data.extend(part)
    return bytes(data)


def receive(channel):
    size, = struct.unpack('!I', read_exact(channel, 4))
    if not 0 < size <= MAX_REQUEST: raise ValueError('Invalid local inference message size')
    return json.loads(read_exact(channel, size))


def send(channel, data):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > MAX_REQUEST: raise ValueError('Local inference message too large')
    channel.sendall(struct.pack('!I',len(payload))+payload)


def serve(fd, device, speech_model='', ocr_model=''):
    import numpy as np
    with socket.socket(fileno=fd) as channel:
        recognizer = LocalRecognizer(device, speech_model=speech_model, ocr_model=ocr_model)
        # Heavy initialization precedes the ready event; never misreport it as
        # inference latency. A missing GPU/model fails closed.
        try:
            recognizer.load_speech(); recognizer.load_ocr()
            send(channel, {'ready':True,'device':recognizer.device,'device_name':recognizer.device_name})
        except Exception as exc:
            print(type(exc).__name__+': '+str(exc)[:600], file=sys.stderr)
            send(channel, {'error':'Local model initialization failed; run local-ai setup/doctor'})
            return
        while True:
            try: request = receive(channel)
            except EOFError: break
            try:
                raw = base64.b64decode(request['data'], validate=True)
                if request['kind'] == 'speech':
                    result = recognizer.speech(raw)
                elif request['kind'] == 'image':
                    w,h = request['width'],request['height']
                    if type(w) is not int or type(h) is not int or not 1<=w<=2048 or not 1<=h<=2048 or len(raw)!=w*h*3:
                        raise ValueError('Invalid RGB image')
                    result = recognizer.image(np.frombuffer(raw,dtype=np.uint8).reshape(h,w,3))
                else: raise ValueError('Unknown local recognition request')
                send(channel, result)
            except Exception as exc:
                print(type(exc).__name__+': '+str(exc)[:600], file=sys.stderr)
                send(channel, {'error':'Local recognition failed; inspect the local worker log'})


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--fd',type=int,required=True)
    parser.add_argument('--device',default='A770'); parser.add_argument('--speech-model',default='')
    parser.add_argument('--ocr-model',default=''); args=parser.parse_args()
    serve(args.fd,args.device,args.speech_model,args.ocr_model)
