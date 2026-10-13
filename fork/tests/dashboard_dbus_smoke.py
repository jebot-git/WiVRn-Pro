#!/usr/bin/env python3
"""Run with dbus-run-session: real QtDBus against an isolated mock WiVRn."""
import json, os, sys, threading, time
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
import gi
gi.require_version('Gio','2.0')
from gi.repository import Gio, GLib
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'dashboard/shora'))
from PySide6.QtWidgets import QApplication
from wivrn_dashboard import ServerClient, StreamingPanel
from motoc_panel import MotocPanel
from shora_qt import ControlClient
xml='''<node><interface name="io.github.wivrn.Server">
<property name="JsonConfiguration" type="s" access="readwrite"/>
<property name="KnownKeysJson" type="s" access="read"/>
<property name="KnownKeys" type="a(ssx)" access="read"/>
<property name="ClientModel" type="s" access="read"/>
<property name="HeadsetConnected" type="b" access="read"/>
<property name="SessionRunning" type="b" access="read"/>
<property name="PairingEnabled" type="b" access="read"/>
<property name="Pin" type="s" access="read"/>
<property name="WayVRStatus" type="s" access="read"/>
<method name="RestartWayVR"/>
<method name="EnablePairing"><arg type="i" direction="in"/><arg type="s" direction="out"/></method>
</interface></node>'''
props={'ClientModel':GLib.Variant('s','seacliff'), 'HeadsetConnected':GLib.Variant('b',True), 'WayVRStatus': GLib.Variant('s','Waiting'), 'JsonConfiguration': GLib.Variant('s','{"encoder":"vaapi","unknown":{"keep":true}}'),
 'KnownKeysJson':GLib.Variant('s',json.dumps([['Quest Pro','fake-key',123]])),
 'KnownKeys':GLib.Variant('a(ssx)',[('Quest Pro','fake-key',123)]),
 'SessionRunning':GLib.Variant('b',False),'PairingEnabled':GLib.Variant('b',False),'Pin':GLib.Variant('s','')}
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
bus.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','RequestName',GLib.Variant('(su)',('io.github.wivrn.Server',0)),None,Gio.DBusCallFlags.NONE,3000,None)
def method(connection,sender,path,iface,name,args,invocation):
    if name == 'RestartWayVR':
        props['WayVRStatus'] = GLib.Variant('s','Restarting')
        invocation.return_value(GLib.Variant('()', ())); return
    assert name=='EnablePairing' and args.unpack()==(120,)
    props['PairingEnabled']=GLib.Variant('b',True); props['Pin']=GLib.Variant('s','123456')
    invocation.return_value(GLib.Variant('(s)',('123456',)))
def get(connection,sender,path,iface,name): return props[name]
def set_(connection,sender,path,iface,name,value): props[name]=value; return True
reg=bus.register_object('/io/github/wivrn/Server',Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],method,get,set_)
loop=GLib.MainLoop(); thread=threading.Thread(target=loop.run,daemon=True);thread.start()
app=QApplication([]); client=ServerClient(); panel=StreamingPanel(client)
motoc=MotocPanel(ControlClient(), panel)
errors=[];client.failure.connect(errors.append)
def wait(check):
    until=time.monotonic()+5
    while time.monotonic()<until:
        app.processEvents()
        if check(): return
        time.sleep(.01)
    raise AssertionError(('Timed out',errors,panel.status.text(),panel.keys.count()))
try:
    wait(lambda: panel.original is not None)
    props['ClientModel'] = GLib.Variant('s','steam-frame'); client.refresh()
    wait(lambda: motoc.mode.view().isRowHidden(motoc.mode.findData('anchors')))
    props['ClientModel'] = GLib.Variant('s','seacliff'); client.refresh()
    wait(lambda: not motoc.mode.view().isRowHidden(motoc.mode.findData('anchors')))
    panel.headset_profile.setCurrentIndex(panel.headset_profile.findData('auto'))
    client.method('RestartWayVR'); wait(lambda:'Restarting' in panel.wayvr_status.text())
    assert panel.keys.count()==1, f'KnownKeys decode failed: {panel.keys.count()}'
    panel.tcp.setChecked(True);panel.save()
    wait(lambda:json.loads(props['JsonConfiguration'].unpack()).get('tcp-only'))
    assert json.loads(props['JsonConfiguration'].unpack())['unknown']=={'keep':True}
    wait(lambda: panel.original.get('tcp-only'))
    panel.lighthouse_controllers.setChecked(False); motoc.save_controllers.click()
    wait(lambda: json.loads(props['JsonConfiguration'].unpack()).get('motoc-lighthouse-controllers') is False)
    wait(lambda: 'settings saved' in motoc.controller_notice.text())
    assert json.loads(props['JsonConfiguration'].unpack())['unknown']=={'keep':True}
    client.method('EnablePairing',120);wait(lambda:'123456' in panel.pairing.text())
    client.save({'encoder':'vulkan'},{'stale':True});wait(lambda:bool(errors))
    assert 'changed elsewhere' in errors[-1]
    props['JsonConfiguration']=GLib.Variant('s','null')
    panel.dirty=False; client.refresh(); wait(lambda:panel.original=={})
    panel.tcp.setChecked(True); panel.save()
    wait(lambda:json.loads(props['JsonConfiguration'].unpack())=={'tcp-only':True})
    print('PASS QtDBus status, paired keys, Motoc controller toggle, config save/preservation/conflict, pairing')
finally:
    client.timer.stop();panel.close();bus.unregister_object(reg);loop.quit();thread.join(timeout=2)
