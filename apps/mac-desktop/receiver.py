#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""One remote Mac desktop as a normal WayVR-owned Qt window.

Prototype: JPEG/15 fps over SSH. No direct socket listener on the Linux side,
no local input injection, and no automatic rerouting on connection loss.
"""
import argparse
import json
import sys
import time
from PySide6.QtCore import Qt, QProcess, QTimer, QRectF, QBuffer, QByteArray, QIODevice, QEvent
from PySide6.QtGui import QPainter, QImageReader, QColor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from protocol import FrameParser, INFO, JPEG, STATUS, WIDTH, HEIGHT, command, ssh_arguments

# Qt keys -> macOS virtual keycodes; positional keys are translated by macOS.
KEYS = {
    'A':0,'S':1,'D':2,'F':3,'H':4,'G':5,'Z':6,'X':7,'C':8,'V':9,'B':11,
    'Q':12,'W':13,'E':14,'R':15,'Y':16,'T':17,'1':18,'2':19,'3':20,'4':21,
    '6':22,'5':23,'=':24,'9':25,'7':26,'-':27,'8':28,'0':29,']':30,'O':31,
    'U':32,'[':33,'I':34,'P':35,'L':37,'J':38,"'":39,'K':40,';':41,'\\':42,
    ',':43,'/':44,'N':45,'M':46,'.':47,'`':50,
}
SPECIAL = {Qt.Key.Key_Return:36,Qt.Key.Key_Enter:76,Qt.Key.Key_Tab:48,
    Qt.Key.Key_Backtab:48,Qt.Key.Key_Space:49,Qt.Key.Key_Backspace:51,Qt.Key.Key_Escape:53,
    Qt.Key.Key_Meta:55,Qt.Key.Key_Shift:56,Qt.Key.Key_Alt:58,Qt.Key.Key_Control:59,
    Qt.Key.Key_Left:123,Qt.Key.Key_Right:124,Qt.Key.Key_Down:125,Qt.Key.Key_Up:126,
    Qt.Key.Key_Delete:117,Qt.Key.Key_Home:115,Qt.Key.Key_End:119,
    Qt.Key.Key_PageUp:116,Qt.Key.Key_PageDown:121,
    Qt.Key.Key_F1:122,Qt.Key.Key_F2:120,Qt.Key.Key_F3:99,Qt.Key.Key_F4:118,
    Qt.Key.Key_F5:96,Qt.Key.Key_F6:97,Qt.Key.Key_F7:98,Qt.Key.Key_F8:100,
    Qt.Key.Key_F9:101,Qt.Key.Key_F10:109,Qt.Key.Key_F11:103,Qt.Key.Key_F12:111}
BUTTONS = {Qt.MouseButton.LeftButton:0,Qt.MouseButton.RightButton:1,Qt.MouseButton.MiddleButton:2}

class Relay(QWidget):
    def __init__(self, host, user, *, autoconnect=True):
        super().__init__()
        self.setWindowTitle(f'Mac desktop · {host}')
        self.resize(1280, 760)
        self.host, self.user = host, user
        self.parser = FrameParser()
        self.sequence = 0
        self.ready = False
        self.image = None
        self.last_frame = 0.0
        self.keys = set()
        self.buttons = set()
        self.process = QProcess(self)
        self.process.setProgram('ssh')
        self.process.setArguments(ssh_arguments(host, user))
        self.process.readyReadStandardOutput.connect(self.read_frames)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.disconnected)
        self.process.errorOccurred.connect(lambda _: self.fail(self.process.errorString()))
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.status = QLabel('Disconnected')
        self.reconnect = QPushButton('Connect')
        self.reconnect.clicked.connect(self.connect_remote)
        row.addWidget(self.status, 1)
        row.addWidget(self.reconnect)
        layout.addLayout(row)
        self.canvas = Canvas(self)
        layout.addWidget(self.canvas, 1)
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(400)
        self.heartbeat.timeout.connect(self.tick)
        self.heartbeat.start()
        if autoconnect:
            QTimer.singleShot(0, self.connect_remote)

    def connect_remote(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.parser = FrameParser()
        self.sequence = 0
        self.ready = False
        self.image = None
        self.status.setText('Connecting over SSH…')
        self.reconnect.setEnabled(False)
        self.process.start()

    def send(self, op, **values):
        if self.process.state() != QProcess.ProcessState.Running:
            return
        if self.process.bytesToWrite() > 65536:
            self.fail('Input channel stalled')
            return
        self.sequence += 1
        self.process.write(command(self.sequence, op, **values))

    def release(self):
        self.send('focus', active=False)
        self.keys.clear()
        self.buttons.clear()

    def tick(self):
        if self.ready:
            if time.monotonic() - self.last_frame > 3:
                self.fail('Desktop stream stalled; input released')
                return
            self.send('ping')

    def fail(self, message):
        # Killing SSH closes the peer and releases its input lease, even when
        # the write queue is blocked. Do not recurse through send/release.
        self.keys.clear()
        self.buttons.clear()
        self.ready = False
        self.image = None
        self.status.setText(message[:300])
        self.canvas.update()
        self.process.kill()
        self.reconnect.setEnabled(True)

    def disconnected(self, *args):
        self.ready = False
        self.keys.clear()
        self.buttons.clear()
        self.image = None
        if self.status.text().startswith(('Connecting', 'Connected')):
            self.status.setText('Disconnected — reconnect explicitly to resume')
        self.reconnect.setEnabled(True)
        self.canvas.update()

    def read_error(self):
        error = bytes(self.process.readAllStandardError()).decode(errors='replace').strip()
        if error:
            self.status.setText(error[-300:])

    def read_frames(self):
        try:
            # Qt processes ready events frequently; discard old images from this batch.
            frames = self.parser.feed(bytes(self.process.read(65536)))
            latest = None
            for kind, payload in frames:
                if kind == INFO:
                    info = json.loads(payload)
                    if (info.get('width'), info.get('height'), info.get('codec')) != (WIDTH, HEIGHT, 'jpeg'):
                        raise ValueError('Unsupported desktop format')
                    self.ready = True
                    self.last_frame = time.monotonic()
                    self.status.setText('Connected · 1920×1080 · click desktop to control · Ctrl+Alt+Esc releases')
                elif kind == STATUS:
                    self.status.setText(str(json.loads(payload).get('message', ''))[:300])
                elif kind == JPEG and self.ready:
                    latest = payload
            if latest is not None:
                buffer = QBuffer()
                buffer.setData(QByteArray(latest))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                reader = QImageReader(buffer, b'JPEG')
                if reader.size().width() != WIDTH or reader.size().height() != HEIGHT:
                    raise ValueError('Unexpected image dimensions')
                image = reader.read()
                if image.isNull():
                    raise ValueError('Invalid desktop image')
                self.image = image
                self.last_frame = time.monotonic()
                self.canvas.update()
            if self.process.bytesAvailable():
                QTimer.singleShot(0, self.read_frames)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            self.fail(str(error))

    def closeEvent(self, event):
        self.release()
        self.process.closeWriteChannel()
        self.process.terminate()
        if not self.process.waitForFinished(300):
            self.process.kill()
            self.process.waitForFinished(300)
        super().closeEvent(event)

class Canvas(QWidget):
    def __init__(self, relay):
        super().__init__(relay)
        self.relay = relay
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 180)

    def bounds(self):
        scale = min(self.width() / WIDTH, self.height() / HEIGHT)
        return QRectF((self.width()-WIDTH*scale)/2, (self.height()-HEIGHT*scale)/2, WIDTH*scale, HEIGHT*scale)

    def point(self, event):
        box = self.bounds()
        p = event.position()
        if box.width() <= 0 or box.height() <= 0:
            return None
        return max(0.0,min(1.0,(p.x()-box.x())/box.width())), max(0.0,min(1.0,(p.y()-box.y())/box.height()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#10151e'))
        if self.relay.image is not None:
            painter.drawImage(self.bounds(), self.relay.image)
        else:
            painter.setPen(QColor('#cdd8e8'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Mac desktop offline')

    def focusInEvent(self, event):
        if self.relay.ready:
            self.relay.send('focus', active=True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.relay.release()
        super().focusOutEvent(event)

    def mouseMoveEvent(self, event):
        if self.hasFocus() and self.relay.ready and (point := self.point(event)):
            self.relay.send('move', x=point[0], y=point[1])

    def mousePressEvent(self, event):
        if not self.relay.ready or event.button() not in BUTTONS or not self.bounds().contains(event.position()):
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # Explicitly reacquire after a connection or peer-side focus release.
        self.relay.send('focus', active=True)
        self.mouseMoveEvent(event)
        button = BUTTONS[event.button()]
        self.relay.buttons.add(button)
        self.relay.send('button', button=button, down=True)

    def mouseReleaseEvent(self, event):
        button = BUTTONS.get(event.button())
        if button in self.relay.buttons:
            self.relay.send('button', button=button, down=False)
            self.relay.buttons.discard(button)

    def wheelEvent(self, event):
        if self.hasFocus() and self.relay.ready:
            delta = event.angleDelta()
            self.relay.send('scroll', x=max(-10,min(10,delta.x()/120)), y=max(-10,min(10,delta.y()/120)))

    def event(self, event):
        # QWidget normally consumes Tab for local focus traversal.
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and self.relay.ready:
            if event.type() == QEvent.Type.KeyPress:
                self.keyPressEvent(event)
            else:
                self.keyReleaseEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.isAutoRepeat() or not self.relay.ready:
            return
        if event.key() == Qt.Key.Key_Escape and event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.clearFocus()
            return
        code = SPECIAL.get(event.key(), KEYS.get(chr(event.key()) if 0 <= event.key() < 128 else ''))
        if code is not None:
            self.relay.keys.add(code)
            self.relay.send('key', code=code, down=True)
            event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        code = SPECIAL.get(event.key(), KEYS.get(chr(event.key()) if 0 <= event.key() < 128 else ''))
        if code in self.relay.keys:
            self.relay.send('key', code=code, down=False)
            self.relay.keys.discard(code)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    ssh_arguments(args.host, args.user)
    app = QApplication(sys.argv)
    app.setApplicationName('Mac Desktop Relay')
    app.setDesktopFileName('mac-desktop-relay')
    window = Relay(args.host, args.user)
    window.show()
    sys.exit(app.exec())
