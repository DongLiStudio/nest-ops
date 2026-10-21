import contextlib,hashlib,importlib.util,io,json,runpy,shutil,sqlite3,sys,tempfile,unittest
from contextlib import closing
from datetime import datetime,timezone
from subprocess import CompletedProcess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector',ROOT/'collectors/linux/collector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class CollectorTests(unittest.TestCase):
    def test_absent_docker_unknown(self):
        with patch.object(m.shutil,'which',return_value=None),patch.object(m.Path,'exists',return_value=False):
            data=m.collect({'services':[{'id':'demo:web','containers':['missing']}]})
        self.assertEqual(data['services']['demo:web']['state'],'unknown')
    def test_stopped_registered_service_down(self):
        from subprocess import CompletedProcess
        with patch.object(m.shutil,'which',return_value='/usr/bin/docker'),patch.object(m,'run',return_value=CompletedProcess([],0,'web|exited|Exited')):
            data=m.collect({'services':[{'id':'demo:web','containers':['web']}]})
        self.assertEqual(data['services']['demo:web']['state'],'down')
    def test_unhealthy_is_degraded(self):
        from subprocess import CompletedProcess
        with patch.object(m.shutil,'which',return_value='/usr/bin/docker'),patch.object(m,'run',return_value=CompletedProcess([],0,'web|running|Up (unhealthy)')):
            data=m.collect({'services':[{'id':'demo:web','containers':['web']}]})
        self.assertEqual(data['services']['demo:web']['state'],'degraded')
class DockerBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.binary = Path(self.temp.name) / 'example docker'
        self.binary.write_text('synthetic executable fixture', encoding='utf-8')
        self.config = {'docker_binary': str(self.binary),
                       'services': [{'id': 'demo:web', 'containers': ['web']}]}

    def test_explicit_executable_overrides_path_without_shell_splitting(self):
        output = CompletedProcess([], 0, 'web|running|Up')
        with patch.object(m.shutil, 'which', return_value='/usr/bin/docker') as discover, \
                patch.object(m.os, 'access', return_value=True), \
                patch.object(m, 'run', return_value=output) as query:
            data = m.collect(self.config)
        discover.assert_not_called()
        query.assert_called_once_with([str(self.binary), 'ps', '-a', '--format',
                                       '{{.Names}}|{{.State}}|{{.Status}}'])
        self.assertEqual(data['services']['demo:web']['state'], 'up')

    def test_unavailable_explicit_binary_does_not_fall_back(self):
        for binary in [None, False, 42, [], '', 'relative/docker', 'docker --context other',
                       str(self.binary.parent / 'missing'), str(self.binary.parent)]:
            with self.subTest(binary=binary), \
                    patch.object(m.shutil, 'which', return_value='/usr/bin/docker') as discover, \
                    patch.object(m.os, 'access', return_value=True), patch.object(m, 'run') as query:
                data = m.collect({**self.config, 'docker_binary': binary})
            discover.assert_not_called()
            query.assert_not_called()
            self.assertEqual(data['services']['demo:web']['state'], 'unknown')

    def test_non_executable_explicit_file_is_unknown(self):
        with patch.object(m.shutil, 'which', return_value='/usr/bin/docker') as discover, \
                patch.object(m.os, 'access', return_value=False), patch.object(m, 'run') as query:
            data = m.collect(self.config)
        discover.assert_not_called()
        query.assert_not_called()
        self.assertEqual(data['services']['demo:web']['state'], 'unknown')

    def test_failed_explicit_docker_query_is_unknown_without_fallback(self):
        for output in [None, CompletedProcess([], 1, '')]:
            with self.subTest(output=output), \
                    patch.object(m.shutil, 'which', return_value='/usr/bin/docker') as discover, \
                    patch.object(m.os, 'access', return_value=True), \
                    patch.object(m, 'run', return_value=output) as query:
                data = m.collect(self.config)
            discover.assert_not_called()
            self.assertEqual(query.call_count, 1)
            self.assertEqual(data['services']['demo:web']['state'], 'unknown')

    def test_default_local_binary_fallback_is_preserved(self):
        config = {'services': self.config['services']}
        with patch.object(m.shutil, 'which', return_value=None), \
                patch.object(m.Path, 'exists', return_value=True), \
                patch.object(m, 'run', return_value=CompletedProcess([], 0, 'web|running|Up')) as query:
            data = m.collect(config)
        self.assertEqual(query.call_args.args[0][0], '/usr/local/bin/docker')
        self.assertEqual(data['services']['demo:web']['state'], 'up')

    def test_unreadable_explicit_path_is_unknown(self):
        with patch.object(m.shutil, 'which', return_value='/usr/bin/docker') as discover, \
                patch.object(m.Path, 'is_file', side_effect=PermissionError), \
                patch.object(m, 'run') as query:
            data = m.collect(self.config)
        discover.assert_not_called()
        query.assert_not_called()
        self.assertEqual(data['services']['demo:web']['state'], 'unknown')


class BackupJobTests(unittest.TestCase):
    NOW = 1767226500

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = Path(self.temp.name) / 'last-success.json'
        self.job = {'timer': 'example-backup.timer', 'service': 'example-backup.service',
                    'receipt': str(self.receipt), 'max_age_seconds': 93600}
        self.timer = {'ActiveState': 'active', 'UnitFileState': 'enabled'}
        self.service = {'ActiveState': 'inactive', 'Result': 'success', 'ExecMainStatus': '0',
                        'ExecMainExitTimestampMonotonic': '123'}
        self.write_receipt()

    def write_receipt(self, age=60, **overrides):
        receipt = {'profile': 'demo-1', 'snapshot': 'example-snapshot', 'checked': True,
                   'utc': datetime.fromtimestamp(self.NOW - age, timezone.utc).isoformat()}
        receipt.update(overrides)
        self.receipt.write_text(json.dumps(receipt), encoding='utf-8')

    def systemctl(self, args):
        self.assertEqual(args[:2], ['systemctl', 'show'])
        self.assertIn(args[2], (self.job['timer'], self.job['service']))
        properties = self.timer if args[2] == self.job['timer'] else self.service
        if properties is None:
            return None
        if isinstance(properties, int):
            return CompletedProcess(args, properties, '')
        return CompletedProcess(args, 0, '\n'.join(k + '=' + v for k, v in properties.items()))

    def state(self, job=None):
        config = {'server': 'demo-1', 'services': [
            {'id': 'demo-1:backup', 'backup_job': self.job if job is None else job}]}
        with patch.object(m.shutil, 'which', return_value=None), \
                patch.object(m.Path, 'exists', return_value=False), \
                patch.object(m, 'run', side_effect=self.systemctl), \
                patch.object(m.time, 'time', return_value=self.NOW):
            return m.collect(config)['services']['demo-1:backup']['state']

    def test_successful_idle_job_is_up(self):
        self.assertEqual(self.state(), 'up')

    def test_reboot_does_not_discard_durable_success(self):
        self.service['ExecMainExitTimestampMonotonic'] = '0'
        self.assertEqual(self.state(), 'up')

    def test_failed_latest_attempt_overrides_success_receipt(self):
        for active, result, status in [('failed', 'exit-code', '1'),
                                       ('inactive', 'exit-code', '0'),
                                       ('inactive', 'success', '2')]:
            with self.subTest(active=active, result=result, status=status):
                self.service.update(ActiveState=active, Result=result, ExecMainStatus=status)
                self.assertEqual(self.state(), 'down')

    def test_timer_must_be_enabled_and_active(self):
        for active, enabled in [('active', 'disabled'), ('inactive', 'enabled'),
                                ('failed', 'enabled'), ('active', 'masked')]:
            with self.subTest(active=active, enabled=enabled):
                self.timer.update(ActiveState=active, UnitFileState=enabled)
                self.assertEqual(self.state(), 'down')

    def test_runtime_enabled_timer_is_enabled(self):
        self.timer['UnitFileState'] = 'enabled-runtime'
        self.assertEqual(self.state(), 'up')

    def test_receipt_age_boundary_and_expiry(self):
        for age, expected in [(93600, 'up'), (93601, 'degraded')]:
            with self.subTest(age=age):
                self.write_receipt(age=age)
                self.assertEqual(self.state(), expected)

    def test_missing_receipt_is_unknown(self):
        self.receipt.unlink()
        self.assertEqual(self.state(), 'unknown')

    def test_invalid_json_and_non_object_receipts_are_unknown(self):
        for data in ['{', 'null', '[]', '"not an object"']:
            with self.subTest(data=data):
                self.receipt.write_text(data, encoding='utf-8')
                self.assertEqual(self.state(), 'unknown')

    def test_undecodable_receipt_is_unknown(self):
        self.receipt.write_bytes(bytes([255, 254]))
        self.assertEqual(self.state(), 'unknown')

    def test_default_age_limit_is_26_hours(self):
        job = dict(self.job)
        del job['max_age_seconds']
        self.write_receipt(age=93601)
        self.assertEqual(self.state(job), 'degraded')

    def test_receipt_requires_checked_profile_and_snapshot(self):
        for override in [{'checked': False}, {'checked': 1}, {'checked': 'true'}, {'profile': 'other-host'},
                         {'snapshot': ''}, {'snapshot': '  '}, {'snapshot': 123},
                         {'utc': None}]:
            with self.subTest(override=override):
                self.write_receipt(**override)
                self.assertEqual(self.state(), 'unknown')

    def test_timestamp_requires_valid_utc_timezone(self):
        for stamp in ['2026-01-01', '2026-01-01T00:14:00',
                      '2026-01-01T08:14:00+08:00', 'invalid']:
            with self.subTest(stamp=stamp):
                self.write_receipt(utc=stamp)
                self.assertEqual(self.state(), 'unknown')

    def test_utc_z_timestamp_is_supported(self):
        self.write_receipt(utc=datetime.fromtimestamp(
            self.NOW - 60, timezone.utc).isoformat().replace('+00:00', 'Z'))
        self.assertEqual(self.state(), 'up')

    def test_future_clock_tolerance(self):
        for age, expected in [(-300, 'up'), (-301, 'unknown')]:
            with self.subTest(age=age):
                self.write_receipt(age=age)
                self.assertEqual(self.state(), expected)

    def test_running_retry_uses_receipt_instead_of_old_failure(self):
        self.service.update(ActiveState='active', Result='exit-code', ExecMainStatus='1')
        self.assertEqual(self.state(), 'up')
        self.write_receipt(age=93601)
        self.assertEqual(self.state(), 'degraded')
        self.receipt.unlink()
        self.assertEqual(self.state(), 'unknown')

    def test_starting_job_is_not_failed_by_previous_attempt(self):
        self.service.update(ActiveState='activating', Result='exit-code', ExecMainStatus='1')
        self.assertEqual(self.state(), 'up')

    def test_unavailable_or_incomplete_systemd_observation_is_unknown(self):
        for target, value in [('timer', None), ('service', None), ('timer', 1), ('service', 1),
                              ('timer', {}), ('service', {}),
                              ('service', {'ActiveState': 'inactive'})]:
            with self.subTest(target=target, value=value):
                old = getattr(self, target)
                setattr(self, target, value)
                try:
                    self.assertEqual(self.state(), 'unknown')
                finally:
                    setattr(self, target, old)

    def test_invalid_job_configuration_is_unknown(self):
        for job in [False, {}, {'timer': 'example-backup.timer'}]:
            with self.subTest(job=job):
                self.assertEqual(self.state(job), 'unknown')
        for maximum in [False, 0, -1, '93600', float('inf'), float('nan'), 10 ** 1000]:
            with self.subTest(maximum=maximum):
                self.assertEqual(self.state({**self.job, 'max_age_seconds': maximum}), 'unknown')


class CollectorConfigurationTests(unittest.TestCase):
    def test_generated_scoped_config_preserves_backup_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'runtime/console').mkdir(parents=True)
            script = root / 'scripts/add_collector.py'
            shutil.copyfile(ROOT / 'scripts/add_collector.py', script)
            job = {'timer': 'example-backup.timer', 'service': 'example-backup.service',
                   'receipt': '/var/lib/example-backup/last-success.json',
                   'max_age_seconds': 93600}
            registry = {'servers': [{'id': 'demo-1'}], 'services': [
                {'id': 'demo-1:backup', 'server': 'demo-1', 'name': 'Daily backup',
                 'backup_job': job},
                {'id': 'other:backup', 'server': 'other', 'backup_job': job}]}
            (root / 'runtime/console/registry.json').write_text(json.dumps(registry))
            (root / 'runtime/console/tokens.json').write_text('{}')
            with patch.object(sys, 'argv', [str(script), 'demo-1', 'https://dashboard.example.com']), \
                    contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(script), run_name='__main__')
            config = json.loads((root / 'runtime/collector-demo-1.json').read_text())
            self.assertEqual(config['services'], [{'id': 'demo-1:backup', 'backup_job': job}])
            self.assertEqual(config['server'], 'demo-1')


class BackrestMonitorTests(unittest.TestCase):
    NOW = 1792569600

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = {'scheduler': 'backrest', 'service': 'example-backrest.service',
                    'config': str(self.root/'config.json'), 'oplog': str(self.root/'oplog.sqlite'),
                    'plan': 'example-daily', 'receipt': str(self.root/'last-success.json')}
        self.config = {'instance': 'demo-1', 'repos': [{'id': 'site', 'guid': 'guid-1'}],
                       'plans': [{'id': 'example-daily', 'repo': 'site',
                                  'schedule': {'cron': '15 2 * * *'}}]}
        self.write_config()
        self.write_receipt()
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.executescript('''
                CREATE TABLE operation_groups (ogid INTEGER,instance_id TEXT,
                    plan_id TEXT,repo_guid TEXT,original_instance_keyid TEXT);
                CREATE TABLE operations (id INTEGER,ogid INTEGER,status INTEGER,
                    snapshot_id TEXT,start_time_ms INTEGER,operation BLOB);
                INSERT INTO operation_groups VALUES(1,'demo-1','example-daily','guid-1','');
                INSERT INTO operation_groups VALUES(2,'other','example-daily','guid-1','remote');
            ''')
        self.add_operation(1, status=3)
        self.service = {'ActiveState': 'active', 'UnitFileState': 'enabled'}

    def write_config(self):
        Path(self.job['config']).write_text(json.dumps(self.config), encoding='utf-8')

    def write_receipt(self, age=30, **changes):
        receipt = {'profile': 'demo-1', 'snapshot': 'snapshot-1', 'checked': True,
                   'utc': datetime.fromtimestamp(self.NOW-age, timezone.utc).isoformat()}
        receipt.update(changes)
        Path(self.job['receipt']).write_text(json.dumps(receipt), encoding='utf-8')

    def add_operation(self, ident, status, blob=b'\xa2\x06\x00', group=1, snapshot='snapshot-1'):
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?)',
                       (ident, group, status, snapshot, (self.NOW-120+ident)*1000, blob))
            db.commit()

    def state(self):
        with patch.object(m, 'systemd_properties', return_value=self.service), \
                patch.object(m.time, 'time', return_value=self.NOW):
            return m.backrest_job_state(self.job, 'demo-1')

    def test_true_backup_and_matching_full_check_receipt_is_up(self):
        before = hashlib.sha256(Path(self.job['oplog']).read_bytes()).digest()
        self.assertEqual(self.state(), 'up')
        self.assertEqual(hashlib.sha256(Path(self.job['oplog']).read_bytes()).digest(), before)

    def test_latest_real_failure_overrides_fresh_success(self):
        for status in (4, 5, 6):
            self.add_operation(2+status, status)
            self.assertEqual(self.state(), 'down')

    def test_hook_success_cannot_mask_failed_backup(self):
        self.add_operation(2, 4)
        self.add_operation(3, 3, b'\xd2\x06\x00')
        self.assertEqual(self.state(), 'down')

    def test_remote_import_and_pending_do_not_override_local_execution(self):
        self.add_operation(2, 4, group=2)
        self.add_operation(3, 1)
        self.assertEqual(self.state(), 'up')

    def test_dry_run_success_or_failure_does_not_replace_real_backup(self):
        for status in (3, 4):
            with self.subTest(status=status):
                self.add_operation(2 + status, status, b'\xa2\x06\x02\x28\x01', snapshot='')
                self.assertEqual(self.state(), 'up')

    def test_dry_run_alone_is_unknown(self):
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.execute('DELETE FROM operations')
            db.commit()
        self.add_operation(2, 3, b'\xa2\x06\x02\x28\x01')
        self.assertEqual(self.state(), 'unknown')

    def test_dry_run_cannot_mask_real_backup_failure(self):
        self.add_operation(2, 4)
        self.add_operation(3, 3, b'\xa2\x06\x02\x28\x01')
        self.assertEqual(self.state(), 'down')

    def test_running_backup_preserves_last_checked_copy(self):
        self.add_operation(2, 2, snapshot='')
        self.assertEqual(self.state(), 'up')

    def test_service_disabled_or_stopped_is_down(self):
        for active, enabled in [('inactive', 'enabled'), ('active', 'disabled')]:
            self.service.update(ActiveState=active, UnitFileState=enabled)
            self.assertEqual(self.state(), 'down')

    def test_disabled_plan_even_false_oneof_is_down(self):
        for value in (True, False):
            self.config['plans'][0]['schedule'] = {'disabled': value}
            self.write_config()
            self.assertEqual(self.state(), 'down')

    def test_wrong_host_and_missing_plan_are_unknown(self):
        self.config['instance'] = 'other'
        self.write_config()
        self.assertEqual(self.state(), 'unknown')
        self.config['instance'] = 'demo-1'
        self.config['plans'] = []
        self.write_config()
        self.assertEqual(self.state(), 'unknown')

    def test_full_check_required_for_same_latest_snapshot(self):
        for changes in ({'snapshot': 'old'}, {'checked': False}, {'checked': 1},
                        {'profile': 'other'}):
            self.write_receipt(**changes)
            self.assertEqual(self.state(), 'unknown')

    def test_expired_copy_is_degraded_future_unknown(self):
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.execute('UPDATE operations SET start_time_ms=?', ((self.NOW-94000)*1000,))
            db.commit()
        self.write_receipt(age=93601)
        self.assertEqual(self.state(), 'degraded')
        self.write_receipt(age=-301)
        self.assertEqual(self.state(), 'unknown')

    def test_warning_is_degraded_even_fresh_old_success(self):
        self.add_operation(2, 7)
        self.assertEqual(self.state(), 'degraded')

    def test_broken_history_is_unknown(self):
        self.add_operation(2, 3, b'\xa2\x06\x80')
        self.assertEqual(self.state(), 'unknown')
        Path(self.job['oplog']).write_bytes(b'not sqlite')
        self.assertEqual(self.state(), 'unknown')

    def test_proto_parser_skips_scalars_and_requires_backup_kind(self):
        self.assertEqual(m._backrest_operation_kind(b'\x08\x01\xa2\x06\x00'), 100)
        self.assertEqual(m._backrest_operation_kind(b'\xd2\x06\x00'), 106)
        self.assertEqual(m._backrest_operation_kind(b'\xa2\x06\x02\x28\x00'), 100)
        self.assertIsNone(m._backrest_operation_kind(b'\xa2\x06\x02\x28\x01'))
        for blob in (b'\x00', b'\xff', b'\xa2\x06\x03\x00', b'\xa2\x06\x01\x28'):
            with self.assertRaises(ValueError):
                m._backrest_operation_kind(blob)

    def test_backrest_dispatch_preserves_existing_collector_contract(self):
        with patch.object(m, 'systemd_properties', return_value=self.service), \
                patch.object(m.time, 'time', return_value=self.NOW), \
                patch.object(m, 'docker_command', return_value=None):
            result = m.collect({'server': 'demo-1', 'services': [
                {'id': 'demo-1:backup', 'backup_job': self.job}]})
        self.assertEqual(result['services']['demo-1:backup']['state'], 'up')


if __name__=='__main__':unittest.main()
