# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QIODevice, QEvent, Qt
from PySide6.QtGui import QImage, QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
from protocol import FrameParser, HEADER, INFO, JPEG, MAX_PAYLOAD, ssh_arguments, command
from receiver import Relay


def frame(kind, data):
    return HEADER.pack(b'WVRD', 1, kind, len(data)) + data


class ProtocolTests(unittest.TestCase):
    def test_fragmented_and_coalesced_frames(self):
        wire = frame(INFO, b'{}') + frame(JPEG, b'jpeg')
        for split in range(len(wire) + 1):
            parser = FrameParser()
            self.assertEqual(parser.feed(wire[:split]) + parser.feed(wire[split:]), [(INFO, b'{}'), (JPEG, b'jpeg')])
            self.assertFalse(parser.buffer)

    def test_invalid_headers_rejected_before_payload(self):
        for magic, version, kind, size in [(b'NOPE',1,1,5),(b'WVRD',2,1,5),(b'WVRD',1,9,5),(b'WVRD',1,1,0),(b'WVRD',1,2,MAX_PAYLOAD+1)]:
            with self.assertRaises(ValueError):
                FrameParser().feed(HEADER.pack(magic,version,kind,size))

    def test_ssh_target_cannot_inject_options_or_shell(self):
        for host, user in [('-oProxyCommand=bad','user'),('host;bad','user'),('host','user;bad'),('host','Display Name')]:
            with self.assertRaises(ValueError):
                ssh_arguments(host, user)
        args = ssh_arguments('192.168.1.100','veravalterova')
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('BatchMode=yes', args)
        self.assertNotIn('AAAAC3', ' '.join(args))

    def test_oversized_read_rejected(self):
        with self.assertRaises(ValueError):
            FrameParser().feed(b'x' * 65537)

    def test_commands_do_not_allow_nonfinite_values(self):
        self.assertEqual(json.loads(command(1,'move',x=.5,y=.5)), dict(seq=1,op='move',x=.5,y=.5))
        with self.assertRaises(ValueError):
            command(1,'move',x=float('nan'),y=0)


class FakeProcess:
    class ProcessState:
        Running = 2
    def __init__(self, incoming=b'', stalled=False):
        self.incoming = incoming
        self.stalled = stalled
        self.killed = False
        self.writes = []
    def state(self):
        from PySide6.QtCore import QProcess
        return QProcess.ProcessState.Running
    def bytesToWrite(self): return 65537 if self.stalled else 0
    def bytesAvailable(self): return len(self.incoming)
    def read(self, size):
        chunk, self.incoming = self.incoming[:size], self.incoming[size:]
        return chunk
    def write(self, data): self.writes.append(json.loads(data))
    def kill(self): self.killed = True


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.relay = Relay('mac.local','user',autoconnect=False)
        self.relay.heartbeat.stop()
        self.original_process = self.relay.process
        self.relay.process = FakeProcess()
    def tearDown(self):
        self.relay.process = self.original_process
        self.relay.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    def test_backpressure_clears_input_without_recursive_release(self):
        self.relay.process.stalled = True
        self.relay.keys.add(56)
        self.relay.buttons.add(0)
        self.relay.send('ping')
        self.assertTrue(self.relay.process.killed)
        self.assertFalse(self.relay.keys | self.relay.buttons)
    def test_focus_loss_releases_input(self):
        self.relay.keys.add(55)
        self.relay.buttons.add(0)
        self.relay.canvas.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertEqual(self.relay.process.writes[-1]['op'], 'focus')
        self.assertFalse(self.relay.process.writes[-1]['active'])
        self.assertFalse(self.relay.keys | self.relay.buttons)
    def test_tab_routes_to_mac(self):
        self.relay.ready = True
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            self.relay.canvas.event(QKeyEvent(kind, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier))
        self.assertEqual([(m['code'],m['down']) for m in self.relay.process.writes],[(48,True),(48,False)])
    def test_frame_decode_and_reject_wrong_dimensions(self):
        for width, height in [(1920,1080),(16,16)]:
            image = QImage(width,height,QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.blue)
            output = QBuffer()
            output.open(QIODevice.OpenModeFlag.WriteOnly)
            self.assertTrue(image.save(output,'JPEG'))
            info = json.dumps(dict(width=1920,height=1080,codec='jpeg')).encode()
            self.relay.process.incoming = frame(INFO,info) + frame(JPEG,bytes(output.data()))
            while self.relay.process.bytesAvailable(): self.relay.read_frames()
            if width==1920:
                self.assertEqual(self.relay.image.size(),image.size())
            else:
                self.assertTrue(self.relay.process.killed)
                self.assertIsNone(self.relay.image)
    def test_stalled_stream_releases(self):
        self.relay.ready = True
        self.relay.last_frame = 0
        self.relay.tick()
        self.assertTrue(self.relay.process.killed)
        self.assertFalse(self.relay.ready)

if __name__ == '__main__': unittest.main()
