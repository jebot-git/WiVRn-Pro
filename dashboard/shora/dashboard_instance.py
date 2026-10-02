# SPDX-License-Identifier: GPL-3.0-or-later
"""One settings window per login session, shared by KDE and WayVR."""
import os
from pathlib import Path
from PySide6.QtCore import QObject, Slot, ClassInfo
from PySide6.QtDBus import QDBusConnection, QDBusMessage

SERVICE = 'io.github.wivrn.ProDashboard'
OBJECT = '/io/github/wivrn/ProDashboard'

@ClassInfo(**{'D-Bus Interface': SERVICE})
class DashboardInstance(QObject):
    def __init__(self, bus=None):
        super().__init__()
        self.bus = bus or QDBusConnection.sessionBus()
        self.window = None
        self.surface = 'vr' if os.environ.get('WAYVR_DISPLAY_AUTH') or os.environ.get('WIVRN_DASHBOARD_SURFACE') == 'vr' else 'desktop'
        self.owner = False

    def legacy_running(self):
        # Migration: retain a pre-single-instance window, including any unsaved
        # settings, until the user closes it. New instances use D-Bus atomically.
        script = str(Path(__file__).with_name('wivrn_dashboard.py').resolve()).encode()
        try:
            installed = Path(__file__).stat().st_mtime
            boot = next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
            ticks = os.sysconf('SC_CLK_TCK')
        except (OSError, ValueError, StopIteration): return False
        for proc in Path('/proc').iterdir():
            if not proc.name.isdecimal() or int(proc.name) == os.getpid(): continue
            try:
                if proc.stat().st_uid != os.getuid(): continue
                fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
                started = boot + int(fields[19]) / ticks
                if started >= installed - 1: continue  # current-build contenders use D-Bus
                args = (proc / 'cmdline').read_bytes().split(b'\0')
                if script in args[:3]: return True
            except (OSError, ValueError): pass
        return False

    def acquire(self):
        if not self.bus.isConnected():
            raise RuntimeError('Cannot contact the login session bus; refusing to open duplicate Settings.')
        self.owner = self.bus.registerService(SERVICE)
        if self.owner and self.legacy_running():
            self.bus.unregisterService(SERVICE)
            self.owner = False
            print('WiVRn Pro Settings is already open from the previous build; keeping that window.', flush=True)
            return False
        if self.owner:
            if not self.bus.registerObject(OBJECT, self, QDBusConnection.RegisterOption.ExportAllSlots):
                self.bus.unregisterService(SERVICE)
                self.owner = False
                raise RuntimeError('Cannot register the Settings window.')
            return True
        reply = self.bus.interface().isServiceRegistered(SERVICE)
        if not reply.isValid() or not reply.value():
            raise RuntimeError('Cannot reserve the Settings window: ' + self.bus.lastError().message())
        # No second QWidget is constructed, even if it targets another compositor.
        request = QDBusMessage.createMethodCall(SERVICE, OBJECT, SERVICE, 'Activate')
        request.setArguments([self.surface])
        self.bus.send(request)
        print('WiVRn Pro Settings is already open; keeping it on its current display.', flush=True)
        return False

    @Slot(str)
    def Activate(self, requested_surface):
        # Never pull a desktop window into VR or reveal a VR window on Linux.
        if self.window is not None and requested_surface == self.surface:
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()

    @Slot(result=str)
    def Surface(self):
        return self.surface

    def close(self):
        if self.owner:
            self.bus.unregisterObject(OBJECT)
            self.bus.unregisterService(SERVICE)
            self.owner = False
