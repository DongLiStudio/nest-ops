import importlib.util,json,os,tempfile,time,unittest
import io,threading,concurrent.futures
from unittest.mock import patch
from pathlib import Path
root=Path(__file__).parent
tmp=tempfile.TemporaryDirectory();os.environ['OPS_DATA']=tmp.name
tokens=Path(tmp.name)/'tokens.json';tokens.write_text('{}');os.environ['OPS_TOKENS']=str(tokens)
spec=importlib.util.spec_from_file_location('console_server',root/'server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sid=next(iter(m.SERVICES))
class HistoryTests(unittest.TestCase):
    def call_ingest(self,payload=None,*,host='demo-host',token='secret',size=None,raw=None):
        body=raw if raw is not None else json.dumps({} if payload is None else payload).encode()
        handler=m.Handler.__new__(m.Handler)
        handler.path='/ingest'
        handler.headers={'X-Server-Profile':host,'Authorization':'Bearer '+token,
                         'Content-Length':str(len(body) if size is None else size)}
        handler.rfile=io.BytesIO(body)
        with patch.object(handler,'send') as send:
            handler.do_POST()
        return send.call_args.args

    def backup_service(self,service_id='demo-host:backup',host='demo-host',max_age=93600,
                       transport=False):
        job={'timer':'demo.timer','service':'demo.service',
             'receipt':'/var/lib/demo/success.json','max_age_seconds':max_age}
        if transport:
            job['transport_receipt']='/var/lib/demo/transport.json'
        return {'id':service_id,'server':host,'name':'Demo backup','category':'ops',
                'app':False,'href':None,'containers':[],'units':[],
                'backup_job':job}

    def backup_payload(self,now,snapshot='snapshot-valid',checked_at=None,
                       transport=None):
        value={'schema_version':1,'checked_at':now if checked_at is None else checked_at,
               'snapshot_id':snapshot,'check_mode':'read-data'}
        if transport is not None:
            value['transport']=transport
        return value

    def test_service_names_include_host_for_shared_tools_and_duplicates(self):
        hosts={'a':{'name':'Example A'},'b':{'name':'Example B'}}
        services={str(i):{'name':name,'server':host} for i,(name,host) in enumerate([
            ('1Panel','a'),('1Panel','b'),('Clash','a'),('Portal','a'),('Portal','b'),
            ('Unique App','a'),('Clash · Example A','a')])}
        m.qualify_service_names(services,hosts)
        self.assertEqual([s['name'] for s in services.values()],[
            '1Panel · Example A','1Panel · Example B','Clash · Example A',
            'Portal · Example A','Portal · Example B','Unique App','Clash · Example A'])
        before=json.dumps(services)
        m.qualify_service_names(services,hosts)
        self.assertEqual(json.dumps(services),before)

    def test_probe_error_is_classified_without_secret_text(self):
        import socket,ssl
        for error,kind in [(socket.gaierror('private.example/secret'),'dns'),(TimeoutError('secret'),'timeout'),(ssl.SSLCertVerificationError('secret'),'tls_certificate'),(ConnectionResetError('secret'),'connection_reset')]:
            self.assertEqual(m.probe_error(m.urllib.error.URLError(error)),kind)
    def test_probe_retry_preserves_raw_failure_without_alert(self):
        service={'id':sid,'name':'Demo','probe':{'url':'https://example.invalid/'}}
        response=io.BytesIO();response.status=200
        opener=unittest.mock.Mock();opener.open.side_effect=[TimeoutError('SECRET'),response]
        with patch.object(m.urllib.request,'build_opener',return_value=opener),patch.object(m.time,'sleep'):
            m.probe(service)
        self.assertEqual(opener.open.call_count,2)
        rows=m.DB.execute('SELECT state,kind FROM probe_attempts WHERE service=? ORDER BY id',(sid,)).fetchall()
        self.assertEqual(rows,[('down','timeout'),('up','http')])
        self.assertEqual(m.DB.execute('SELECT state FROM spans ORDER BY id DESC LIMIT 1').fetchone()[0],'up')
        self.assertEqual(m.DB.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM probe_notifiable').fetchone()[0],0)
        self.assertNotIn('SECRET',str(m.DB.execute('SELECT detail FROM spans').fetchall()))
    def test_probe_alert_after_three_cycles_and_recovery_once(self):
        service={'id':sid};now=time.time()
        for i in range(3):
            state=m.probe_cycle(service,False,'timeout',now=now+i*60)
            self.assertEqual(state,'degraded' if i<2 else 'down')
            self.assertEqual(m.DB.execute('SELECT count(*) FROM probe_notifiable').fetchone()[0],int(i==2))
        m.probe_cycle(service,False,'timeout',now=now+185)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM probe_notifiable').fetchone()[0],1)
        m.probe_cycle(service,True,'HTTP 200',now=now+190)
        m.probe_cycle(service,True,'HTTP 200',now=now+200)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM probe_notifiable').fetchone()[0],2)
        self.assertEqual([r[0] for r in m.DB.execute('SELECT state FROM spans ORDER BY id')],
                         ['degraded','down','up'])
    def test_probe_failure_streak_breaks_on_success_and_gap(self):
        service={'id':sid};now=time.time()
        self.assertEqual(m.probe_cycle(service,False,now=now),'degraded')
        self.assertEqual(m.probe_cycle(service,True,now=now+60),'up')
        self.assertEqual(m.probe_cycle(service,False,now=now+120),'degraded')
        self.assertEqual(m.probe_cycle(service,False,now=now+400),'degraded')
        self.assertEqual(m.DB.execute('SELECT failures FROM probe_alert_state WHERE service=?',(sid,)).fetchone()[0],1)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM probe_notifiable').fetchone()[0],0)
    def test_healthy_probe_cycles_do_not_create_events_or_notices(self):
        service={'id':sid}
        for offset in (0,60,120):
            self.assertEqual(m.probe_cycle(service,True,'HTTP 200',now=1000+offset),'up')
        self.assertEqual(m.DB.execute('SELECT count(*) FROM events').fetchone()[0],0)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM notices').fetchone()[0],0)
    def test_probe_notification_gate_and_no_orphan_recovery(self):
        service={'id':sid,'probe':{'url':'https://example.invalid/'}}
        config=Path(tmp.name)/'notify-test.json'
        config.write_text(json.dumps(dict(sender='monitor@example.invalid',recipient='owner@example.invalid',address='smtps://smtp.example.invalid:465',username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp;smtp.send_message.return_value={}
        now=time.time()-500
        with patch.dict(m.SERVICES,{sid:service}),patch.dict(os.environ,{'OPS_NOTIFY':str(config)}),patch.object(m.smtplib,'SMTP_SSL',return_value=smtp):
            m.probe_cycle(service,False,now=now)
            m.probe_cycle(service,True,now=now+60)
            m.notify_pending();self.assertEqual(smtp.send_message.call_count,0)
            for i in range(3):
                m.probe_cycle(service,False,now=now+120+i*60)
            m.notify_pending();m.notify_pending();self.assertEqual(smtp.send_message.call_count,1)
            m.probe_cycle(service,True,now=now+310)
            m.notify_pending();m.notify_pending();self.assertEqual(smtp.send_message.call_count,2)
    def test_confirmed_fault_is_not_repeated_across_neutral_states(self):
        config=Path(tmp.name)/'notify-neutral.json'
        config.write_text(json.dumps(dict(sender='monitor@example.invalid',
            recipient='owner@example.invalid',address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        smtp.send_message.return_value={}
        now=time.time()-300
        states=[('down',now),('maintenance',now+30),('unknown',now+60),
                ('down',now+90),('up',now+120)]
        with patch.dict(os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            for state,stamp in states:
                m.record(sid,state,now=stamp)
                with patch.object(m.time,'time',return_value=stamp+1):
                    m.notify_pending()
        self.assertEqual(smtp.send_message.call_count,2)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM notices').fetchone()[0],5)

    def test_maintenance_and_unknown_transitions_do_not_send_notifications(self):
        config=Path(tmp.name)/'notify-neutral-only.json'
        config.write_text(json.dumps(dict(sender='monitor@example.invalid',
            recipient='owner@example.invalid',address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        smtp.send_message.return_value={}
        now=time.time()-300
        with patch.dict(os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            for state,offset in [('up',0),('maintenance',30),('unknown',60)]:
                m.record(sid,state,now=now+offset)
                with patch.object(m.time,'time',return_value=now+offset+1):
                    m.notify_pending()
        self.assertEqual(smtp.send_message.call_count,0)
        self.assertEqual(m.DB.execute(
            "SELECT count(*) FROM notices WHERE status='skipped'").fetchone()[0],2)
    def test_statistics_exclude_backup_maintenance_from_availability_and_incidents(self):
        result=m.statistics([(0,60,'up'),(60,90,'maintenance')],0,90)
        self.assertEqual(result['availability'],100.0)
        self.assertEqual(result['coverage'],100.0)
        self.assertEqual(result['known_seconds'],90)
        self.assertEqual(result['down_seconds'],0)
        self.assertEqual(result['maintenance_seconds'],30)
    def test_smtp_errors_are_sanitized_and_classified(self):
        import socket,ssl
        cases=[
            (socket.gaierror('SECRET-HOST'),'dns'),
            (TimeoutError('SECRET-TIMEOUT'),'timeout'),
            (ssl.SSLError('SECRET-TLS'),'tls'),
            (m.smtplib.SMTPAuthenticationError(535,b'SECRET-AUTH'),'auth'),
            (ConnectionResetError('SECRET-CONNECTION'),'connection'),
            (ValueError('SECRET-SEND'),'send')]
        for error,kind in cases:
            with self.subTest(kind=kind):
                self.assertEqual(m.smtp_error_kind(error),kind)
                self.assertNotIn('SECRET',kind)
        self.assertEqual([row[1] for row in m.DB.execute('PRAGMA table_info(notices)')][-1],
                         'error_kind')
    def test_smtp_send_failure_keeps_failed_status_without_error_text(self):
        import socket
        service=dict(m.SERVICES[sid],probe={'url':'https://example.invalid/'})
        config=Path(tmp.name)/'notify-failure.json'
        config.write_text(json.dumps(dict(sender='monitor@example.invalid',
            recipient='owner@example.invalid',address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        now=time.time()-500
        with patch.dict(m.SERVICES,{sid:service}),patch.dict(os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',side_effect=socket.gaierror('SECRET-HOST')):
            for i in range(3):
                m.probe_cycle(service,False,'入口检查失败 · dns',now=now+i*60)
            m.notify_pending()
        event_id=m.DB.execute('SELECT down_event FROM probe_alert_state WHERE service=?',(sid,)).fetchone()[0]
        status,error_kind=m.DB.execute('SELECT status,error_kind FROM notices WHERE event=?',(event_id,)).fetchone()
        self.assertEqual((status,error_kind),('failed','dns'))
        self.assertNotIn('SECRET',' '.join(str(row) for row in m.DB.execute('SELECT * FROM notices')))
    def test_event_pagination_beyond_200_and_stable_anchor(self):
        now=time.time()
        with m.DB:
            for i in range(240):m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'up','down','test')",(sid,now-i))
            m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES('retired',?,'up','down','hidden')",(now,))
        first=m.event_page('24h',1,15)
        self.assertEqual(first['total'],240);self.assertEqual(first['pages'],16)
        with m.DB:m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'down','up','new')",(sid,first['before']+1))
        last=m.event_page('24h',16,15,sid,first['before'])
        self.assertEqual(last['total'],240);self.assertEqual(len(last['items']),15)
        self.assertFalse(set(e['id'] for e in first['items']) & set(e['id'] for e in last['items']))
        self.assertEqual(m.event_page('24h',999)['page'],16)
    def test_event_pagination_validation_and_window(self):
        with m.DB:m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'up','down','old')",(sid,time.time()-2*86400))
        self.assertEqual(m.event_page('24h')['total'],0)
        self.assertEqual(m.event_page('7d')['total'],1)
        for args in [('invalid',1,15),('24h',0,15),('24h',1,101)]:
            with self.assertRaises(ValueError):m.event_page(*args)
        with self.assertRaises(ValueError):m.event_page(service='retired')
    def test_event_page_keeps_recovery_across_neutral_states(self):
        now=time.time()
        with m.DB:
            down=m.DB.execute("INSERT INTO events(service,time,old,state,detail) "
                              "VALUES(?,?,'up','down','failed')",(sid,now-30)).lastrowid
            m.DB.execute("INSERT INTO events(service,time,old,state,detail) "
                         "VALUES(?,?,'down','maintenance','running')",(sid,now-20))
            m.DB.execute("INSERT INTO events(service,time,old,state,detail) "
                         "VALUES(?,?,'maintenance','unknown','gap')",(sid,now-10))
            recovered=m.DB.execute("INSERT INTO events(service,time,old,state,detail) "
                                   "VALUES(?,?,'unknown','up','recovered')",(sid,now)).lastrowid
        page=m.event_page('24h',1,15,sid)
        self.assertEqual([item['id'] for item in page['items']],[recovered,down])
        self.assertEqual(m.previous_notifiable_state(sid,recovered),'down')
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
        with m.LOCK,m.DB:
            for table in ('probe_attempts','probe_alert_state','probe_notifiable',
                          'hosts','host_health','backup_metadata','service_channels'):
                m.DB.execute('DELETE FROM '+table)
            m.DB.execute('DELETE FROM spans')
            m.DB.execute('DELETE FROM events')
            m.DB.execute('DELETE FROM notices')
        m.SNAPSHOT_CACHE.clear();m.HOUSEHOLD_CACHE=None
        with m.LOCK,m.DB:m.DB.execute('DELETE FROM samples')
    def test_ingest_rejects_missing_and_wrong_tokens_without_writes(self):
        payload={'services':{sid:{'state':'up'}},'metrics':{'load1':0.5}}
        with patch.dict(m.TOKENS,{'demo-host':'secret'},clear=True):
            for token in ('', 'wrong'):
                with self.subTest(token=token):
                    self.assertEqual(self.call_ingest(payload,token=token)[0],401)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM hosts').fetchone()[0],0)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM spans').fetchone()[0],0)

    def test_ingest_rejects_cross_host_service_and_does_not_leak_metrics(self):
        own='host-a:web';other='host-b:web';own_host='host-a';other_host='host-b'
        services={
            own:{'id':own,'server':own_host,'name':'A','category':'ops',
                 'app':False,'href':None,'containers':[],'units':[]},
            other:{'id':other,'server':other_host,'name':'B','category':'ops',
                   'app':False,'href':None,'containers':[],'units':[]},
        }
        with patch.dict(m.SERVICES,services,clear=True),patch.dict(
                m.TOKENS,{own_host:'one',other_host:'two'},clear=True):
            status=self.call_ingest({'services':{other:{'state':'down'}}},
                                    host=own_host,token='one')[0]
            self.assertEqual(status,400)
            self.assertEqual(self.call_ingest(
                {'services':{},'metrics':{'load1':11,'disk_percent':20}},
                host=own_host,token='one')[0],200)
            self.assertEqual(self.call_ingest(
                {'services':{},'metrics':{'load1':22,'memory_percent':30}},
                host=other_host,token='two')[0],200)
        self.assertEqual(json.loads(m.DB.execute(
            'SELECT metrics FROM hosts WHERE id=?',(own_host,)).fetchone()[0]),
            {'load1':11,'disk_percent':20})
        self.assertEqual(json.loads(m.DB.execute(
            'SELECT metrics FROM hosts WHERE id=?',(other_host,)).fetchone()[0]),
            {'load1':22,'memory_percent':30})
        self.assertEqual(m.DB.execute(
            'SELECT count(*) FROM spans WHERE service=?',(other,)).fetchone()[0],0)

    def test_ingest_accepts_only_whitelisted_metrics_and_invalidates_snapshot(self):
        host=m.SERVICES[sid]['server']
        with patch.dict(m.TOKENS,{host:'secret'},clear=True):
            initial=m.snapshot('24h')
            status,_=self.call_ingest(
                {'services':{sid:{'state':'up'}},
                 'metrics':{'load1':0.5,'memory_percent':30,'disk_percent':40,
                            'uptime_seconds':50,'unexpected':999,'token':'SECRET'}},
                host=host)
            self.assertEqual(status,200)
        self.assertEqual(json.loads(m.DB.execute(
            'SELECT metrics FROM hosts WHERE id=?',(host,)).fetchone()[0]),
            {'load1':0.5,'memory_percent':30.0,'disk_percent':40.0,'uptime_seconds':50.0})
        refreshed=m.snapshot('24h')
        self.assertIsNot(initial,refreshed)
        self.assertEqual(next(s for s in refreshed['services'] if s['id']==sid)['state'],'up')

    def test_ingest_rejects_oversized_body(self):
        host=m.SERVICES[sid]['server']
        with patch.dict(m.TOKENS,{host:'secret'},clear=True):
            status,body=self.call_ingest(
                {'services':{},'metrics':{}},host=host,size=32769,
                raw=b'x'*32769)
        self.assertEqual(status,400)
        self.assertFalse(body.get('accepted',True))
        self.assertEqual(m.DB.execute('SELECT count(*) FROM hosts').fetchone()[0],0)

    def test_ingest_schema_rejects_unknown_fields_and_is_atomic(self):
        host=m.SERVICES[sid]['server']
        invalid=[
            {'services':{},'metrics':{},'unexpected':True},
            {'services':{},'metrics':[]},
            {'metrics':{}},
            {'services':{'missing:service':{'state':'up'}},'metrics':{}},
            {'services':{sid:{'state':'up','note':'private'}},'metrics':{}},
            {'services':{sid:{'state':'up','backup':{}}},'metrics':{}},
        ]
        with patch.dict(m.TOKENS,{host:'secret'},clear=True):
            for payload in invalid:
                with self.subTest(payload=payload):
                    status,body=self.call_ingest(payload,host=host)
                    self.assertEqual(status,400)
                    self.assertFalse(body.get('accepted',True))
        self.assertEqual(m.DB.execute('SELECT count(*) FROM hosts').fetchone()[0],0)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM spans').fetchone()[0],0)

    def test_ingest_allows_probe_with_one_local_target_but_rejects_probe_only(self):
        host='demo-host'
        mixed_id='demo-host:mixed'
        probe_only_id='demo-host:probe-only'
        services={
            mixed_id:{
                'id':mixed_id,'server':host,'name':'Mixed','category':'ops',
                'app':False,'href':None,'containers':['web'],'units':[],
                'probe':{'url':'https://example.invalid/'},
            },
            probe_only_id:{
                'id':probe_only_id,'server':host,'name':'Probe','category':'ops',
                'app':False,'href':None,'containers':[],'units':[],
                'probe':{'url':'https://example.invalid/'},
            },
        }
        with patch.dict(m.SERVICES,services,clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            status,body=self.call_ingest(
                {'services':{mixed_id:{'state':'up'}}},host=host)
            self.assertEqual(status,200)
            self.assertTrue(body.get('accepted'))
            self.assertEqual(m.DB.execute(
                'SELECT state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',
                (mixed_id,)).fetchone()[0],'unknown')
            rejected,body=self.call_ingest(
                {'services':{probe_only_id:{'state':'up'}}},host=host)
            self.assertEqual(rejected,400)
            self.assertFalse(body.get('accepted',True))
            self.assertEqual(m.DB.execute(
                'SELECT count(*) FROM spans WHERE service=?',
                (probe_only_id,)).fetchone()[0],0)

    def test_probe_and_host_channels_cannot_overwrite_each_other(self):
        host='demo-host';service_id='demo-host:mixed';now=time.time()-10
        service={
            'id':service_id,'server':host,'name':'Mixed','category':'ops',
            'app':False,'href':None,'containers':['web'],'units':[],
            'probe':{'url':'https://example.invalid/'},
        }
        with patch.dict(m.SERVICES,{service_id:service},clear=True):
            self.assertEqual(m.update_service_channel(
                service_id,'host','up','host up',now=now,service=service)[0],'unknown')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','up','HTTP 200',now=now+1,service=service)[0],'up')
            self.assertEqual(m.update_service_channel(
                service_id,'host','down','container down',now=now+2,service=service)[0],'down')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','up','HTTP 200',now=now+3,service=service)[0],'down')
            self.assertEqual(m.update_service_channel(
                service_id,'host','up','host restored',now=now+4,service=service)[0],'up')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','degraded','HTTP timeout',now=now+5,service=service)[0],'degraded')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','up','HTTP 200',now=now+6,service=service)[0],'up')
        self.assertEqual([row[0] for row in m.DB.execute(
            'SELECT state FROM spans WHERE service=? ORDER BY id',(service_id,))],
            ['unknown','up','down','up','degraded','up'])
        events=list(m.DB.execute(
            'SELECT old,state,source FROM events WHERE service=? ORDER BY id',
            (service_id,)))
        self.assertEqual(events,[
            ('unknown','up','aggregate'),
            ('up','down','host'),('down','up','aggregate'),
            ('up','degraded','probe'),('degraded','up','aggregate')])

    def test_channel_age_forces_unknown_until_both_channels_report(self):
        host='demo-host';service_id='demo-host:mixed-age';now=time.time()-300
        service={
            'id':service_id,'server':host,'name':'Mixed','category':'ops',
            'app':False,'href':None,'containers':['web'],'units':[],
            'probe':{'url':'https://example.invalid/'},
        }
        with patch.dict(m.SERVICES,{service_id:service},clear=True):
            m.update_service_channel(service_id,'host','up',now=now,service=service)
            m.update_service_channel(service_id,'probe','up',now=now,service=service)
            m.refresh_channel_aggregates(now=now+151)
            self.assertEqual(m.DB.execute(
                'SELECT state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',
                (service_id,)).fetchone()[0],'unknown')
            self.assertEqual(m.update_service_channel(
                service_id,'host','up',now=now+200,service=service)[0],'unknown')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','up',now=now+201,service=service)[0],'up')

    def test_stale_channel_never_masks_fresh_fault_or_healthy_evidence(self):
        host='demo-host';service_id='demo-host:mixed-stale';now=time.time()-400
        service={
            'id':service_id,'server':host,'name':'Mixed','category':'ops',
            'app':False,'href':None,'containers':['web'],'units':[],
            'probe':{'url':'https://example.invalid/'},
        }
        with patch.dict(m.SERVICES,{service_id:service},clear=True):
            m.update_service_channel(service_id,'host','up',now=now,service=service)
            m.update_service_channel(service_id,'probe','up',now=now,service=service)
            self.assertEqual(m.update_service_channel(
                service_id,'host','down',now=now+250,service=service)[0],'down')
            m.refresh_channel_aggregates(now=now+301)
            self.assertEqual(m.update_service_channel(
                service_id,'host','up',now=now+302,service=service)[0],'unknown')
            self.assertEqual(m.update_service_channel(
                service_id,'probe','up',now=now+303,service=service)[0],'up')

    def test_configured_transport_requires_fresh_valid_evidence(self):
        service_id='backup:transport-required';host='demo-host';now=time.time()-2
        service=self.backup_service(service_id,host,transport=True)
        valid_up={'checked_at':now,'state':'up','unauthenticated_status':401,
                  'config_status':'pinned','error_code':None}
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            for metadata,state in (
                    (self.backup_payload(now,snapshot='snapshot-1'),'unknown'),
                    (self.backup_payload(now,snapshot='snapshot-2',transport={
                        'checked_at':now,'state':'unknown',
                        'unauthenticated_status':None,
                        'config_status':None,'error_code':'probe_error'}),'unknown'),
                    (self.backup_payload(now,snapshot='snapshot-3',
                                         checked_at=now+1,
                                         transport=valid_up),'up')):
                status,body=self.call_ingest({'services':{service_id:{
                    'state':'up','backup':metadata}}},host=host)
                self.assertEqual(status,200)
                self.assertEqual(body['downgraded_backup_states'],
                                 1 if state!='up' else 0)
                self.assertEqual(m.DB.execute(
                    'SELECT state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',
                    (service_id,)).fetchone()[0],state)

    def test_mixed_channels_send_one_failure_and_one_recovery(self):
        host='demo-host';service_id='demo-host:mixed-notify';now=time.time()-500
        service={
            'id':service_id,'server':host,'name':'Mixed','category':'ops',
            'app':False,'href':None,'containers':['web'],'units':[],
            'probe':{'url':'https://example.invalid/'},
        }
        config=Path(tmp.name)/'notify-mixed.json'
        config.write_text(json.dumps(dict(
            sender='monitor@example.invalid',recipient='owner@example.invalid',
            address='smtps://smtp.example.invalid:465',username='test',
            password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        smtp.send_message.return_value={}
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            m.update_service_channel(service_id,'host','up',now=now,service=service)
            m.update_service_channel(service_id,'probe','up',now=now,service=service)
            m.update_service_channel(service_id,'host','down',now=now+1,service=service)
            with patch.object(m.time,'time',return_value=now+2):m.notify_pending()
            m.probe_cycle(service,False,'timeout',now=now+2)
            with patch.object(m.time,'time',return_value=now+3):m.notify_pending()
            m.update_service_channel(service_id,'host','up',now=now+4,service=service)
            with patch.object(m.time,'time',return_value=now+5):m.notify_pending()
            m.probe_cycle(service,True,'HTTP 200',now=now+6)
            with patch.object(m.time,'time',return_value=now+7):m.notify_pending()
        self.assertEqual(smtp.send_message.call_count,2)

    def test_maintenance_state_is_reserved_for_backup_services(self):
        service=next(item for item in m.SERVICES.values()
                     if not item.get('backup_job') and not item.get('probe'))
        host=service['server']
        with patch.dict(m.TOKENS,{host:'secret'},clear=True):
            status,body=self.call_ingest(
                {'services':{service['id']:{'state':'maintenance'}}},host=host)
        self.assertEqual(status,400)
        self.assertFalse(body.get('accepted',True))
        self.assertEqual(m.DB.execute('SELECT count(*) FROM hosts').fetchone()[0],0)
        self.assertEqual(m.DB.execute(
            'SELECT count(*) FROM spans WHERE service=?',
            (service['id'],)).fetchone()[0],0)

    def test_ingest_validates_all_observations_before_writing_any(self):
        host='demo-host';first='demo-host:first';second='demo-host:second'
        services={
            first:{'id':first,'server':host,'name':'First','category':'ops',
                   'app':False,'href':None,'containers':[],'units':[]},
            second:{'id':second,'server':host,'name':'Second','category':'ops',
                    'app':False,'href':None,'containers':[],'units':[]},
        }
        with patch.dict(m.SERVICES,services,clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            status,body=self.call_ingest({'services':{
                first:{'state':'up'},second:{'state':'up','unexpected':True}},
                'metrics':{'load1':0.1}},host=host)
        self.assertEqual(status,400)
        self.assertFalse(body.get('accepted',True))
        self.assertEqual(m.DB.execute('SELECT count(*) FROM hosts').fetchone()[0],0)
        self.assertEqual(m.DB.execute('SELECT count(*) FROM spans').fetchone()[0],0)

    def test_invalid_or_forged_backup_metadata_does_not_replace_last_valid(self):
        service_id='backup:demo'
        host='demo-host'
        service={'id':service_id,'server':host,'name':'Demo backup',
                 'category':'ops','app':False,'href':None,'containers':[],'units':[],
                 'backup_job':{'timer':'demo.timer','service':'demo.service',
                               'receipt':'/var/lib/demo/success.json'}}
        now=time.time()-10
        valid={'backup':{'schema_version':1,'checked_at':now,'snapshot_id':'snapshot-valid',
                         'check_mode':'read-data','transport':{
                             'checked_at':now,'state':'up','unauthenticated_status':401,
                             'config_status':'pinned','error_code':None}}}
        invalid_values=[
            {'schema_version':1,'checked_at':now,'snapshot_id':'snapshot-invalid',
             'check_mode':'metadata'},
            {'schema_version':1,'checked_at':now,'snapshot_id':'snapshot-invalid',
             'check_mode':'read-data','transport':{
                 'checked_at':now,'state':'up','unauthenticated_status':None,
                 'config_status':'pinned','error_code':None}},
            {'schema_version':1,'checked_at':now,'snapshot_id':'snapshot-invalid',
             'check_mode':'read-data','transport':{
                 'checked_at':now,'state':'down','unauthenticated_status':500,
                 'config_status':'pinned','error_code':'tunnel_closed'}},
            {'schema_version':1,'checked_at':now,'snapshot_id':'snapshot-invalid',
             'check_mode':'read-data','receipt_path':'/private/restic/last-success.json'},
        ]
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            status,_=self.call_ingest({'services':{service_id:{
                'state':'up','backup':valid['backup']}}},host=host)
            self.assertEqual(status,200)
            for backup in invalid_values:
                status,body=self.call_ingest({'services':{service_id:{
                    'state':'down','backup':backup}}},host=host)
                self.assertEqual(status,200)
                self.assertEqual(body['rejected_backup_metadata'],1)
        row=m.DB.execute(
            'SELECT metadata_state,snapshot_id,check_mode,metadata_json '
            'FROM backup_metadata WHERE service=? AND host=?',
            (service_id,host)).fetchone()
        self.assertEqual(row[:3],('invalid','snapshot-valid','read-data'))
        self.assertEqual(json.loads(row[3])['snapshot_id'],'snapshot-valid')
        self.assertNotIn('/private',str(row))

    def test_normal_and_maintenance_require_current_fresh_safe_metadata(self):
        host='demo-host';now=time.time()-1
        ids=['backup:missing','backup:maintenance','backup:expired-up',
             'backup:expired-maintenance']
        services={sid:self.backup_service(sid,host,max_age=60) for sid in ids}
        observations={
            ids[0]:{'state':'up'},
            ids[1]:{'state':'maintenance','backup':self.backup_payload(
                now,snapshot='fresh',checked_at=now-30)},
            ids[2]:{'state':'up','backup':self.backup_payload(
                now,snapshot='stale',checked_at=now-61)},
            ids[3]:{'state':'maintenance','backup':self.backup_payload(
                now,snapshot='stale',checked_at=now-61)},
        }
        with patch.dict(m.SERVICES,services,clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            status,body=self.call_ingest({'services':observations},host=host)
            self.assertEqual(status,200)
            self.assertEqual(body['downgraded_backup_states'],3)
            items={item['id']:item for item in m.build_snapshot('24h')['services']}
        self.assertEqual(items[ids[0]]['state'],'unknown')
        self.assertEqual(items[ids[0]]['backup']['metadata_state'],'missing')
        self.assertEqual(items[ids[1]]['state'],'maintenance')
        self.assertEqual(items[ids[1]]['backup']['metadata_state'],'valid')
        self.assertEqual(items[ids[2]]['state'],'down')
        self.assertEqual(items[ids[3]]['state'],'down')

    def test_backup_metadata_cannot_roll_back_newer_snapshot_evidence(self):
        service_id='backup:ordered';host='demo-host';now=time.time()-2
        service=self.backup_service(service_id,host)
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            first=self.backup_payload(now,snapshot='snapshot-new',checked_at=now-10)
            self.assertEqual(self.call_ingest({'services':{
                service_id:{'state':'up','backup':first}}},host=host)[0],200)
            older=self.backup_payload(now,snapshot='snapshot-old',checked_at=now-20)
            status,body=self.call_ingest({'services':{
                service_id:{'state':'up','backup':older}}},host=host)
            self.assertEqual(status,200)
            self.assertEqual(body['rejected_backup_metadata'],1)
            conflicting=self.backup_payload(now,snapshot='snapshot-conflict',
                                            checked_at=now-10)
            status,body=self.call_ingest({'services':{
                service_id:{'state':'up','backup':conflicting}}},host=host)
            self.assertEqual(status,200)
            self.assertEqual(body['rejected_backup_metadata'],1)
        row=m.DB.execute(
            'SELECT metadata_state,snapshot_id,metadata_json FROM backup_metadata '
            'WHERE service=? AND host=?',(service_id,host)).fetchone()
        self.assertEqual(row[:2],('invalid','snapshot-new'))
        self.assertEqual(json.loads(row[2])['snapshot_id'],'snapshot-new')
        self.assertEqual(m.DB.execute(
            'SELECT state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',
            (service_id,)).fetchone()[0],'unknown')

    def test_stale_transport_is_projected_to_backup_abnormal(self):
        service_id='backup:transport';host='demo-host';now=time.time()-1
        service=self.backup_service(service_id,host,transport=True)
        transport={'checked_at':now-901,'state':'up','unauthenticated_status':401,
                   'config_status':'pinned','error_code':None}
        metadata=self.backup_payload(now,snapshot='snapshot-fresh',
                                     checked_at=now-30,transport=transport)
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True):
            status,_=self.call_ingest({'services':{
                service_id:{'state':'up','backup':metadata}}},host=host)
            self.assertEqual(status,200)
            view=m.build_snapshot('24h')
        item=view['services'][0]
        self.assertEqual(item['state'],'down')
        self.assertTrue(item['backup']['transport_stale'])
        self.assertEqual(item['backup']['transport']['state'],'down')
        self.assertEqual(item['backup']['transport']['error_code'],'stale')
        self.assertEqual(view['backup_summary']['up'],0)
        self.assertEqual(view['backup_summary']['abnormal'],1)
        self.assertEqual(view['backup_summary']['transport_down'],1)

    def test_transport_failure_notifies_once_and_unknown_preserves_recovery(self):
        service_id='backup:transport-notify';host='demo-host';now=time.time()-300
        service=self.backup_service(service_id,host,transport=True)
        config=Path(tmp.name)/'notify-transport.json'
        config.write_text(json.dumps(dict(
            sender='monitor@example.invalid',recipient='owner@example.invalid',
            address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        smtp.send_message.return_value={}
        transport_down={'checked_at':now,'state':'down',
                        'unauthenticated_status':None,
                        'config_status':None,'error_code':'tunnel_closed'}
        transport_up={'checked_at':now+180,'state':'up',
                      'unauthenticated_status':401,
                      'config_status':'pinned','error_code':None}
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True),patch.dict(
                os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            for state,stamp,transport in (
                    ('up',now,transport_down),
                    ('maintenance',now+60,transport_down),
                    ('unknown',now+120,transport_down),
                    ('up',now+180,transport_up)):
                payload=self.backup_payload(
                    stamp,snapshot='snapshot-valid',checked_at=now+180,
                    transport=transport)
                status,_=self.call_ingest({'services':{service_id:{
                    'state':state,'backup':payload}}},host=host)
                self.assertEqual(status,200)
                with patch.object(m.time,'time',return_value=stamp+1):
                    m.notify_pending()
        self.assertEqual(smtp.send_message.call_count,2)
        self.assertEqual([row[0] for row in m.DB.execute(
            'SELECT state FROM events WHERE service=? ORDER BY id',
            (service_id,))],['down','up'])

    def test_transport_age_creates_consistent_abnormal_event_and_notice(self):
        service_id='backup:transport-age';host='demo-host';now=time.time()-20
        service=self.backup_service(service_id,host,transport=True)
        config=Path(tmp.name)/'notify-transport-age.json'
        config.write_text(json.dumps(dict(
            sender='monitor@example.invalid',recipient='owner@example.invalid',
            address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        smtp.send_message.return_value={}
        transport_up={'checked_at':now,'state':'up',
                      'unauthenticated_status':401,
                      'config_status':'pinned','error_code':None}
        payload=self.backup_payload(
            now,snapshot='snapshot-age',checked_at=now,transport=transport_up)
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True),patch.dict(
                os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            with patch.object(m.time,'time',return_value=now):
                status,_=self.call_ingest({'services':{service_id:{
                    'state':'up','backup':payload}}},host=host)
            self.assertEqual(status,200)
            stale_at=now+m.TRANSPORT_MAX_AGE+1
            with patch.object(m.time,'time',return_value=stale_at):
                m.refresh_channel_aggregates(now=stale_at)
                m.notify_pending()
                view=m.build_snapshot('24h')
            event=m.DB.execute(
                "SELECT old,state,source FROM events WHERE service=? ORDER BY id DESC LIMIT 1",
                (service_id,)).fetchone()
        item=view['services'][0]
        self.assertEqual(item['state'],'down')
        self.assertEqual(item['backup']['transport']['state'],'down')
        self.assertEqual(item['backup']['transport']['error_code'],'stale')
        self.assertEqual(view['summary']['abnormal'],1)
        self.assertEqual(view['backup_summary']['abnormal'],1)
        self.assertEqual(event,('unknown','down','transport'))
        self.assertEqual(smtp.send_message.call_count,1)
        self.assertEqual(m.DB.execute(
            "SELECT count(*) FROM notices WHERE status='sent'").fetchone()[0],1)

    def test_transport_missing_is_unknown_without_notice(self):
        service_id='backup:transport-missing';host='demo-host';now=time.time()-20
        service=self.backup_service(service_id,host,transport=True)
        config=Path(tmp.name)/'notify-transport-missing.json'
        config.write_text(json.dumps(dict(
            sender='monitor@example.invalid',recipient='owner@example.invalid',
            address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        smtp=unittest.mock.MagicMock();smtp.__enter__.return_value=smtp
        payload=self.backup_payload(now,snapshot='snapshot-missing')
        with patch.dict(m.SERVICES,{service_id:service},clear=True),patch.dict(
                m.TOKENS,{host:'secret'},clear=True),patch.dict(
                os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',return_value=smtp):
            status,_=self.call_ingest({'services':{service_id:{
                'state':'up','backup':payload}}},host=host)
            self.assertEqual(status,200)
            m.refresh_channel_aggregates(now=now+1)
            with patch.object(m.time,'time',return_value=now+1):
                m.notify_pending()
        self.assertEqual(m.DB.execute(
            'SELECT state FROM spans WHERE service=? ORDER BY id DESC LIMIT 1',
            (service_id,)).fetchone()[0],'unknown')
        self.assertEqual(smtp.send_message.call_count,0)

    def test_public_snapshot_hides_private_monitor_configuration_and_flags_service(self):
        private={'id':'private:backup','server':'demo-host','name':'Private backup',
                 'category':'开发与运维','app':False,'href':None,'containers':[],'units':[],
                 'process':'backup-process',
                 'backup_job':{'receipt':'/private/restic/last-success.json',
                               'config':'/private/restic/config.json'},
                 'probe':{'url':'https://private.example.invalid/hidden'}}
        with patch.dict(m.SERVICES,{'private:backup':private},clear=True):
            item=m.build_snapshot('24h')['services'][0]
        self.assertNotIn('backup_job',item)
        self.assertNotIn('probe',item)
        self.assertTrue(item['is_backup'])
        self.assertTrue(item['is_probe'])
        self.assertNotIn('/private/',json.dumps(item))
        self.assertNotIn('private.example',json.dumps(item))
    def test_backup_summary_counts_snapshot_and_transport_without_crashing(self):
        service={'id':'demo:backup','server':'demo-host','name':'Backup',
                 'category':'ops','app':False,'href':None,'containers':[],'units':[],
                 'backup_job':{'timer':'demo.timer','service':'demo.service',
                               'receipt':'/var/lib/demo/health.json'}}
        now=time.time();metadata={'schema_version':1,'checked_at':now-30,
                                  'snapshot_id':'snapshot-1','check_mode':'read-data',
                                  'transport':{'checked_at':now-20,'state':'down',
                                               'unauthenticated_status':None,
                                               'config_status':None,
                                               'error_code':'tunnel_closed'}}
        with patch.dict(m.SERVICES,{'demo:backup':service},clear=True):
            m.update_backup_metadata('demo:backup','demo-host',{'backup':metadata},now)
            summary=m.build_snapshot('24h')['backup_summary']
        self.assertEqual(summary['registered'],1)
        self.assertEqual(summary['metadata_valid'],1)
        self.assertEqual(summary['transport_down'],1)
        self.assertEqual(summary['abnormal'],1)

    def test_smtp_failure_retries_stop_after_five_attempts(self):
        config=Path(tmp.name)/'notify-retry.json'
        config.write_text(json.dumps(dict(sender='monitor@example.invalid',
            recipient='owner@example.invalid',address='smtps://smtp.example.invalid:465',
            username='test',password='fixture')))
        now=time.time()-4000
        with m.DB:
            event_id=m.DB.execute("INSERT INTO events(service,time,old,state,detail) VALUES(?,?,'up','down','test')",(sid,now)).lastrowid
        with patch.dict(os.environ,{'OPS_NOTIFY':str(config)}),patch.object(
                m.smtplib,'SMTP_SSL',side_effect=ConnectionRefusedError('SECRET-CONNECTION')) as smtp:
            for attempt in range(7):
                with patch.object(m.time,'time',return_value=now+attempt*301):
                    m.notify_pending()
        self.assertEqual(smtp.call_count,5)
        status,attempts,error_kind=m.DB.execute(
            'SELECT status,attempts,error_kind FROM notices WHERE event=?',(event_id,)).fetchone()
        self.assertEqual((status,attempts,error_kind),('failed',5,'connection'))
        self.assertNotIn('SECRET',str(m.DB.execute('SELECT * FROM notices').fetchall()))

    def test_snapshot_cache_revision_and_expiry(self):
        with patch.object(m,'build_snapshot',wraps=m.build_snapshot) as build:
            first=m.snapshot('24h');self.assertIs(first,m.snapshot('24h'));self.assertEqual(build.call_count,1)
            m.record(sid,'down');self.assertEqual(m.snapshot('24h')['services'][0]['state'],'down');self.assertEqual(build.call_count,2)
            cached=m.SNAPSHOT_CACHE['24h'];m.SNAPSHOT_CACHE['24h']=(cached[0]-6,*cached[1:])
            m.snapshot('24h');self.assertEqual(build.call_count,3)
    def test_snapshot_no_household_or_latency_wait(self):
        statements=[];m.DB.set_trace_callback(statements.append)
        try:
            with patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('main must not call household')):
                view=m.snapshot('24h')
            self.assertNotIn('household',view);self.assertTrue(all(s['latency']==[] for s in view['services']))
            self.assertFalse(any('FROM samples' in sql for sql in statements))
        finally:m.DB.set_trace_callback(None)
    def test_snapshot_concurrent_build_is_shared(self):
        with patch.object(m,'build_snapshot',wraps=m.build_snapshot) as build:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                values=list(pool.map(lambda _:m.snapshot('24h'),range(4)))
            self.assertEqual(build.call_count,1);self.assertTrue(all(value is values[0] for value in values))
    def test_household_parallel_cache_and_failed_refresh(self):
        barrier=threading.Barrier(2)
        def response(*args,**kwargs):
            barrier.wait(timeout=2);time.sleep(.08);return io.BytesIO(b'{"status":"ONLINE"}')
        with patch.object(m.urllib.request,'urlopen',side_effect=response) as request:
            started=time.perf_counter();first=m.household_snapshot();elapsed=time.perf_counter()-started
            self.assertEqual(first['household']['ups']['status'],'ONLINE');self.assertIs(first,m.household_snapshot());self.assertEqual(request.call_count,2)
            print(f'household concurrent synthetic 2x80ms: {elapsed*1000:.1f}ms')
        m.HOUSEHOLD_CACHE=(m.HOUSEHOLD_CACHE[0]-11,first)
        with patch.object(m.urllib.request,'urlopen',side_effect=OSError('offline')):
            self.assertEqual(m.household_snapshot()['household'],{'ups':{},'ddns':{}})
    def test_latency_is_scoped_to_service_and_last_day(self):
        now=time.time()
        with m.DB:
            m.DB.executemany('INSERT INTO samples VALUES(?,?,?)',[(sid,now-60,50),(sid,now-2*86400,999),('retired',now-60,999)])
        self.assertEqual(m.service_latency(sid)[0][1],50)
        with self.assertRaises(ValueError):m.service_latency('retired')
    def test_new_endpoints_require_session(self):
        handler=m.Handler.__new__(m.Handler)
        for path in ['/household','/service?service='+sid,'/api?window=24h']:
            handler.path=path
            with patch.object(handler,'authenticated',return_value=False),patch.object(handler,'send') as send:
                handler.do_GET();self.assertEqual(send.call_args.args[0],401)
    def test_gzip_negotiation_and_roundtrip(self):
        for header in ['gzip','br, gzip;q=0.8','GZIP']:
            self.assertTrue(m.accepts_gzip(header))
        for header in ['br','gzip;q=0','gzip;q=bad','gzip;q=nan','gzip;q=2']:
            self.assertFalse(m.accepts_gzip(header))
        handler=m.Handler.__new__(m.Handler);handler.headers={'Accept-Encoding':'gzip'};handler.wfile=io.BytesIO()
        payload={'text':'synthetic '*1000}
        with patch.object(handler,'send_response'),patch.object(handler,'send_header') as headers,patch.object(handler,'end_headers'):
            handler.send(200,payload)
            self.assertIn(('Content-Encoding','gzip'),[call.args for call in headers.call_args_list])
            self.assertIn(('Cache-Control','no-store'),[call.args for call in headers.call_args_list])
        self.assertEqual(json.loads(m.gzip.decompress(handler.wfile.getvalue())),payload)
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
