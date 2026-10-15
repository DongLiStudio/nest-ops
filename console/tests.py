import importlib.util,json,os,tempfile,time,unittest
import io
from unittest.mock import patch
from pathlib import Path
root=Path(__file__).parent
tmp=tempfile.TemporaryDirectory();os.environ['OPS_DATA']=tmp.name
tokens=Path(tmp.name)/'tokens.json';tokens.write_text('{}');os.environ['OPS_TOKENS']=str(tokens)
spec=importlib.util.spec_from_file_location('console_server',root/'server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sid=next(iter(m.SERVICES))
class HistoryTests(unittest.TestCase):
    def test_password_and_oidc_sessions(self):
        handler=m.Handler.__new__(m.Handler);handler.headers={'Cookie':'session=opaque'}
        for user in [{'name':'Homepage','id':'homepage'},{'email':'operator@example.com'},{'id':'opaque-subject'}]:
            with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps({'user':user}).encode())):
                self.assertTrue(handler.authenticated())
        for value in [{},{'user':None},{'user':{}},{'user':{'name':' ','email':False,'id':42}},['user']]:
            with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(value).encode())):
                self.assertFalse(handler.authenticated())
    def test_cookie_alone_never_authenticates(self):
        handler=m.Handler.__new__(m.Handler);handler.headers={'Cookie':'session=opaque'}
        with patch.object(m.urllib.request,'urlopen',side_effect=OSError('unreachable')):
            self.assertFalse(handler.authenticated())
        handler.headers={}
        with patch.object(m.urllib.request,'urlopen') as request:
            self.assertFalse(handler.authenticated());request.assert_not_called()
    def test_private_entry_protocol_and_scope(self):
        path=Path(tmp.name)/'entries.json'; services={'one':{'href':None}}
        path.write_text(json.dumps({'one':'hometunnel://connect?host=example.invalid'}))
        m.apply_private_entries(services,path)
        self.assertTrue(services['one']['href'].startswith('hometunnel:'))
        for value in [{'missing':'https://example.invalid'},{'one':'javascript:alert(1)'}]:
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):m.apply_private_entries(services,path)
    def setUp(self):
        with m.LOCK,m.DB:m.DB.execute('DELETE FROM spans');m.DB.execute('DELETE FROM events');m.DB.execute('DELETE FROM notices')
    def test_no_invented_history(self):
        now=time.time();m.record(sid,'up',now=now-60);m.record(sid,'up',now=now)
        stats=m.statistics(m.history(sid,now-86400,now),now-86400,now)
        self.assertEqual(stats['availability'],100);self.assertLess(stats['coverage'],1)
    def test_downtime_and_single_events(self):
        m.record(sid,'up',now=1000);m.record(sid,'down',now=1060);m.record(sid,'down',now=1120);m.record(sid,'up',now=1180)
        stats=m.statistics(m.history(sid,1000,1180),1000,1180)
        self.assertAlmostEqual(stats['availability'],33.333,places=3)
        self.assertEqual(m.DB.execute('select count(*) from events').fetchone()[0],2)
    def test_gap_is_unknown(self):
        m.record(sid,'up',now=1000);m.record(sid,'up',now=1600)
        stats=m.statistics(m.history(sid,1000,1600),1000,1600)
        self.assertEqual(stats['known_seconds'],90);self.assertEqual(stats['coverage'],15)
    def test_initial_fault_once(self):
        m.record(sid,'degraded',now=1000);m.record(sid,'degraded',now=1060)
        self.assertEqual(m.DB.execute('select count(*) from events').fetchone()[0],1)
    def test_invalid_state(self):
        with self.assertRaises(ValueError):m.record(sid,'healthy-but-invented')
    def test_unknown_not_healthy(self):
        stats=m.statistics([],1000,2000);self.assertIsNone(stats['availability']);self.assertEqual(stats['coverage'],0)
    def test_removed_resource_events_not_exposed(self):
        m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'up','down','old resource event')",('retired-resource',time.time()));m.DB.commit();m.record(sid,'down',now=time.time())
        self.assertEqual([e['service'] for e in m.snapshot('24h')['events']],[sid])
    def test_removed_resource_not_notified(self):
        m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'up','down','old resource event')",('retired-resource',time.time()));m.DB.commit();note=Path(tmp.name)/'notify.json';note.write_text('{}');os.environ['OPS_NOTIFY']=str(note)
        try:m.notify_pending()
        finally:os.environ.pop('OPS_NOTIFY',None)
        self.assertEqual(m.DB.execute('SELECT status FROM notices ORDER BY event DESC LIMIT 1').fetchone()[0],'skipped')
    def test_retained_registration(self):
        view=m.snapshot('30d');self.assertEqual(len(view['services']),len(m.SERVICES));self.assertTrue(all(s['state']=='unknown' for s in view['services']))
if __name__=='__main__':
    try:unittest.main()
    finally:m.DB.close();tmp.cleanup()
