import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import audit_campaign as a
class Campaign(unittest.TestCase):
    def plan(self):
        plan=dict(host_ipv4='192.168.1.106',headset_ipv4='192.168.1.105',expected_host_interface='enp113s0',expected_headset_serial='230YC01D8W000E',host_binary={'path':'/tmp/probe'},require_free_receiver_ports=list(range(53169,53179)),optional_smoke=[],runs=[])
        tokens={}
        for i,name in enumerate(a.EXPECTED_NAMES):
            frames,size,hz=(5,32768,20) if name.startswith('smoke') else (60,32768,20) if name.startswith('low') else (180,98304,90)
            transport='tcp' if '-tcp' in name else 'udp';group='smoke' if name.startswith('smoke') else name.split('-')[0]+'-'+name.split('-')[-1];token=tokens.setdefault(group,format(len(tokens)+1,'032x'));port=53169+i
            common=[str(port),token,str(frames),str(size),str(hz)]
            arm=dict(name=name,frames=frames,pair_bytes=size,hz=hz,transport=transport,receiver_port=port,matched_token_group=group,nominal_ipv4_udp_offered_mbps=(size+((size+1159)//1160)*68+84)*8*hz/1e6,sender_argv=['/tmp/probe','send',transport,plan['host_ipv4'],plan['headset_ipv4']]+common,receiver_argv=['{remote}/probe','receive',transport,plan['headset_ipv4'],plan['host_ipv4']]+common)
            plan['optional_smoke' if i<2 else 'runs'].append(arm)
        return plan
    def test_plan(self):self.assertEqual(len(a.check_plan(self.plan())),10)
    def test_control_mutations(self):
        changes=[lambda p:p['runs'].pop(),lambda p:p['runs'].reverse(),lambda p:p.update(host_ipv4='127.0.0.1'),lambda p:p.update(expected_headset_serial='other'),lambda p:p['runs'][0].update(frames=59),lambda p:p['runs'][0].update(nominal_ipv4_udp_offered_mbps=0),lambda p:p['runs'][0].update(matched_token_group='independent'),lambda p:p['runs'][0]['sender_argv'].__setitem__(7,'59'),lambda p:p['runs'][0]['receiver_argv'].__setitem__(6,'0'*32),lambda p:p['runs'][0].update(receiver_port=53169),lambda p:p['require_free_receiver_ports'].pop()]
        for change in changes:
            plan=self.plan();change(plan)
            with self.assertRaises(ValueError):a.check_plan(plan)
    def test_hash_and_status_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'fixture.stdout').write_text('original\n');(d/'fixture.stderr').write_text('');cmd={'label':'fixture','returncode':0,'stdout_sha256':a.sha(d/'fixture.stdout'),'stderr_sha256':a.sha(d/'fixture.stderr')};receipt={'commands':[cmd]}
            self.assertIn('fixture',a.check_logs(receipt,d))
            for mutation in ['status','duplicate','label','host_timeout','log']:
                bad=copy.deepcopy(receipt)
                if mutation=='status':bad['commands'][0]['returncode']=1
                if mutation=='host_timeout':bad['commands'][0]['host_timeout']=True
                if mutation=='duplicate':bad['commands'].append(copy.deepcopy(cmd))
                if mutation=='label':bad['commands'][0]['label']='../escape'
                if mutation=='log':(d/'fixture.stdout').write_text('changed\n')
                with self.assertRaises(ValueError):a.check_logs(bad,d)
    def test_inventory_rejects_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);(d/'file').write_text('x');(d/'alias').symlink_to('file')
            with self.assertRaises(ValueError):a.inventory(d)

class Provenance(unittest.TestCase):
    def test_pinned_binary_join(self):
        qualified={'binary_sha256':{'/tmp/wivrn-wireless-pair-build/'+name:'0'*64 for name in ['probe','probe-android','test-android']}}
        plan={'host_binary':{'sha256':'0'*64},'files':[{'remote':'probe','local':'/arbitrary/new/probe','sha256':'0'*64},{'remote':'grammar-test','local':'/arbitrary/new/test','sha256':'0'*64}]}
        a.qualified_artifacts(plan,qualified)
        for target in ['host','probe','grammar','population']:
            bad=copy.deepcopy(plan)
            if target=='host':bad['host_binary']['sha256']='1'*64
            if target=='probe':bad['files'][0]['sha256']='1'*64
            if target=='grammar':bad['files'][1]['sha256']='1'*64
            if target=='population':bad['files'].append(copy.deepcopy(bad['files'][0]))
            with self.assertRaises(ValueError):a.qualified_artifacts(bad,qualified)
    def test_missing_control_is_not_a_valid_sidecar(self):
        required={'serial':['adb','shell','getprop ro.serialno'],'host-route':['ip','-j','route','get','192.168.1.105'],'hash-0':['adb','shell','sha256sum /owned/probe']}
        commands={key:{'argv':argv} for key,argv in required.items()};a.check_required(commands,required)
        for key in required:
            missing=copy.deepcopy(commands);missing.pop(key)
            with self.assertRaises(ValueError):a.check_required(missing,required)
            changed=copy.deepcopy(commands);changed[key]['argv']=['true']
            with self.assertRaises(ValueError):a.check_required(changed,required)


class Addresses(unittest.TestCase):
    def test_reciprocity_alone_is_insufficient(self):
        plan={'host_ipv4':'192.168.1.106','headset_ipv4':'192.168.1.105'}
        sender={'local_ipv4':plan['host_ipv4'],'peer_ipv4':plan['headset_ipv4']};receiver={'local_ipv4':plan['headset_ipv4'],'peer_ipv4':plan['host_ipv4']}
        a.check_addresses(sender,receiver,plan)
        sender['local_ipv4']=receiver['peer_ipv4']='192.0.2.1'
        with self.assertRaises(ValueError):a.check_addresses(sender,receiver,plan)
if __name__=='__main__':unittest.main()
