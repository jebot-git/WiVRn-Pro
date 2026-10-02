#!/usr/bin/env python3
"""Run under dbus-run-session; no real compositor, settings or service changes."""
import os, sys, subprocess, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'dashboard/shora'))
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from dashboard_instance import DashboardInstance, SERVICE, OBJECT
app=QCoreApplication(sys.argv)
if len(sys.argv)>1:
    guard=DashboardInstance()
    if sys.argv[1]=='owner':
        assert guard.acquire()
        print('ready',flush=True)
        QTimer.singleShot(10000,app.quit)
        app.exec();guard.close()
    else:
        assert not guard.acquire()
    sys.exit(0)
for initial,duplicate in [('desktop','vr'),('vr','desktop')]:
    env=os.environ.copy();env.pop('WAYVR_DISPLAY_AUTH',None);env['WIVRN_DASHBOARD_SURFACE']=initial
    owner=subprocess.Popen([sys.executable,__file__,'owner'],env=env,stdout=subprocess.PIPE,text=True)
    try:
        assert owner.stdout.readline().strip()=='ready'
        for _ in range(2):
            subprocess.run([sys.executable,__file__,'duplicate'],env=env|{'WIVRN_DASHBOARD_SURFACE':duplicate},check=True,timeout=5)
        request=QDBusMessage.createMethodCall(SERVICE,OBJECT,SERVICE,'Surface')
        reply=QDBusConnection.sessionBus().call(request)
        assert reply.arguments()==[initial],reply.errorMessage()
    finally:
        owner.terminate();owner.wait(timeout=5)
    for _ in range(50):
        if not QDBusConnection.sessionBus().interface().isServiceRegistered(SERVICE).value():break
        time.sleep(.02)
guard=DashboardInstance();assert guard.acquire();guard.close()
print('Dashboard: cross-display exclusion, same-process ownership and crash recovery passed')
