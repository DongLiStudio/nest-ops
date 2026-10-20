import contextlib,importlib.util,io,json,runpy,shutil,sys,tempfile,unittest
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


if __name__=='__main__':unittest.main()
