import contextlib,hashlib,importlib.util,io,json,runpy,shutil,sqlite3,sys,tempfile,unittest
from contextlib import closing
from datetime import datetime,timezone
from subprocess import CompletedProcess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector',ROOT/'collectors/linux/collector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
schema_spec=importlib.util.spec_from_file_location('service_schema',ROOT/'scripts/service_schema.py')
schema=importlib.util.module_from_spec(schema_spec);schema_spec.loader.exec_module(schema)
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
        receipt = {'status': 'success', 'profile': 'demo-1', 'snapshot': 'example-snapshot',
                   'checked': True, 'check_mode': 'read-data',
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
        for age, expected in [(93600, 'up'), (93601, 'down')]:
            with self.subTest(age=age):
                self.write_receipt(age=age)
                self.assertEqual(self.state(), expected)

    def test_missing_receipt_is_unknown(self):
        self.receipt.unlink()
        self.assertEqual(self.state(), 'unknown')

    def test_up_is_not_reported_when_safe_metadata_cannot_be_emitted(self):
        self.write_receipt(snapshot='private path/snapshot')
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
        self.assertEqual(self.state(job), 'down')

    def test_receipt_requires_success_checked_read_data_profile_and_snapshot(self):
        cases = [
            ({'status': 'failed'}, 'down'),
            ({'status': None}, 'unknown'),
            ({'checked': False}, 'down'),
            ({'checked': 0}, 'unknown'),
            ({'checked': 1}, 'unknown'),
            ({'checked': 'true'}, 'unknown'),
            ({'checked': []}, 'unknown'),
            ({'check_mode': None}, 'unknown'),
            ({'check_mode': 1}, 'unknown'),
            ({'check_mode': []}, 'unknown'),
            ({'check_mode': ''}, 'unknown'),
            ({'check_mode': 'metadata'}, 'down'),
            ({'profile': 'other-host'}, 'unknown'),
            ({'snapshot': ''}, 'unknown'),
            ({'snapshot': '  '}, 'unknown'),
            ({'snapshot': 123}, 'unknown'),
            ({'utc': None}, 'unknown'),
        ]
        for override, expected in cases:
            with self.subTest(override=override):
                self.write_receipt(**override)
                self.assertEqual(self.state(), expected)

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
        self.assertEqual(self.state(), 'maintenance')
        self.write_receipt(age=93601)
        self.assertEqual(self.state(), 'down')
        self.receipt.unlink()
        self.assertEqual(self.state(), 'unknown')

    def test_atomic_running_receipt_preserves_last_checked_snapshot(self):
        self.service['ActiveState'] = 'active'
        self.write_receipt(status='running')
        self.assertEqual(self.state(), 'maintenance')
        with patch.object(m.time, 'time', return_value=self.NOW):
            metadata = m.receipt_metadata(self.job, 'demo-1')
        self.assertEqual(metadata['snapshot_id'], 'example-snapshot')
        self.assertEqual(metadata['check_mode'], 'read-data')

    def test_starting_job_is_not_failed_by_previous_attempt(self):
        self.service.update(ActiveState='activating', Result='exit-code', ExecMainStatus='1')
        self.assertEqual(self.state(), 'maintenance')

    def test_running_without_a_valid_receipt_is_unknown(self):
        self.service['ActiveState'] = 'active'
        for override in ({'status': 'failed'}, {'checked': False},
                         {'check_mode': None}, {'snapshot': ''}):
            with self.subTest(override=override):
                self.write_receipt(**override)
                expected = 'down' if override in ({'status': 'failed'}, {'checked': False}) else 'unknown'
                self.assertEqual(self.state(), expected)

    def test_running_with_a_stale_receipt_is_down(self):
        self.service['ActiveState'] = 'active'
        self.write_receipt(age=93601)
        self.assertEqual(self.state(), 'down')

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


class TransportMetadataTests(unittest.TestCase):
    NOW = 1792569600

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = Path(self.temp.name) / 'transport.json'

    def write(self, age=30, **changes):
        value = {
            'schema': 1,
            'profile': 'demo-1',
            'status': 'up',
            'checked_utc': datetime.fromtimestamp(
                self.NOW - age, timezone.utc).isoformat(),
            'unauthenticated_status': 401,
            'authenticated_config_status': 'pinned',
            'error_code': None,
        }
        value.update(changes)
        self.receipt.write_text(json.dumps(value), encoding='utf-8')

    def metadata(self):
        return m.transport_metadata(str(self.receipt), 'demo-1', self.NOW)

    def test_fifteen_minute_boundary_and_stale_projection(self):
        self.write(age=900)
        self.assertEqual(self.metadata()['state'], 'up')
        self.assertIsNone(self.metadata()['error_code'])
        self.write(age=901)
        value = self.metadata()
        self.assertEqual(value['state'], 'down')
        self.assertEqual(value['error_code'], 'stale')
        self.assertEqual(set(value), {
            'checked_at', 'state', 'unauthenticated_status', 'config_status',
            'error_code'})

    def test_unknown_stale_receipt_is_also_down(self):
        self.write(age=901, status='unknown', unauthenticated_status=None,
                   authenticated_config_status=None)
        self.assertEqual(self.metadata()['state'], 'down')

    def test_invalid_or_future_transport_receipt_is_explicit_unknown(self):
        self.write(age=-301)
        self.assertEqual(self.metadata(), {
            'checked_at': self.NOW, 'state': 'unknown',
            'unauthenticated_status': None, 'config_status': None,
            'error_code': 'receipt_invalid'})
        self.write(error_code='/private/path')
        self.assertEqual(self.metadata()['state'], 'unknown')
        self.assertEqual(self.metadata()['error_code'], 'receipt_invalid')
        self.write(status='up', unauthenticated_status=None)
        self.assertEqual(self.metadata()['state'], 'unknown')
        self.assertEqual(self.metadata()['error_code'], 'receipt_invalid')

    def test_unconfigured_transport_receipt_is_not_invented(self):
        self.assertIsNone(m.transport_metadata(None, 'demo-1', self.NOW))
        self.assertIsNone(m.transport_metadata('', 'demo-1', self.NOW))

    def test_missing_configured_transport_is_explicit_safe_unknown(self):
        missing = str(Path(self.temp.name) / 'missing.json')
        self.assertEqual(m.transport_metadata(missing, 'demo-1', self.NOW), {
            'checked_at': self.NOW, 'state': 'unknown',
            'unauthenticated_status': None, 'config_status': None,
            'error_code': 'receipt_missing'})

    def test_invalid_configured_transport_is_independent_of_backup_metadata(self):
        self.write(age=-301)
        backup_receipt = Path(self.temp.name) / 'success-invalid-transport.json'
        backup_receipt.write_text(json.dumps({
            'status': 'success', 'profile': 'demo-1', 'snapshot': 'snapshot-1',
            'checked': True, 'check_mode': 'read-data',
            'utc': datetime.fromtimestamp(
                self.NOW - 30, timezone.utc).isoformat()}), encoding='utf-8')
        with patch.object(m.time, 'time', return_value=self.NOW):
            value = m.receipt_metadata({
                'receipt': str(backup_receipt),
                'transport_receipt': str(self.receipt)}, 'demo-1')
            transport = m.transport_metadata(
                str(self.receipt), 'demo-1', self.NOW)
        self.assertEqual(set(value), {
            'schema_version', 'checked_at', 'snapshot_id', 'check_mode'})
        self.assertEqual(transport['state'], 'unknown')
        self.assertEqual(transport['error_code'], 'receipt_invalid')

    def test_backup_metadata_contains_only_allowlisted_safe_fields(self):
        self.write(age=30)
        self.receipt.write_text(json.dumps({
            **json.loads(self.receipt.read_text()),
            'private_path': '/private/restic/key',
            'raw_error': 'private details',
        }), encoding='utf-8')
        backup_receipt = Path(self.temp.name) / 'success.json'
        backup_receipt.write_text(json.dumps({
            'status': 'success', 'profile': 'demo-1', 'snapshot': 'snapshot-1',
            'checked': True, 'check_mode': 'read-data',
            'utc': datetime.fromtimestamp(
                self.NOW - 30, timezone.utc).isoformat(),
            'private_path': '/private/restic/key'}), encoding='utf-8')
        with patch.object(m.time, 'time', return_value=self.NOW):
            value = m.receipt_metadata({
                'receipt': str(backup_receipt),
                'transport_receipt': str(self.receipt)}, 'demo-1')
        self.assertEqual(set(value), {
            'schema_version', 'checked_at', 'snapshot_id', 'check_mode'})
        self.assertNotIn('/private', json.dumps(value))

    def test_backup_failure_does_not_drop_independent_transport_fault(self):
        backup_receipt = Path(self.temp.name) / 'failed-backup.json'
        backup_receipt.write_text(json.dumps({
            'status': 'failed', 'profile': 'demo-1',
            'snapshot': 'snapshot-1', 'checked': True,
            'check_mode': 'read-data',
            'utc': datetime.fromtimestamp(
                self.NOW - 30, timezone.utc).isoformat()}), encoding='utf-8')
        self.write(status='down', unauthenticated_status=None,
                   authenticated_config_status=None,
                   error_code='tunnel_closed')
        service = {'id': 'demo-1:backup', 'backup_job': {
            'timer': 'demo.timer', 'service': 'demo.service',
            'receipt': str(backup_receipt),
            'transport_receipt': str(self.receipt),
            'max_age_seconds': 93600}}
        with patch.object(m.shutil, 'which', return_value=None), \
                patch.object(m.Path, 'exists', return_value=False), \
                patch.object(m, 'systemd_properties', side_effect=[
                    {'ActiveState': 'active', 'UnitFileState': 'enabled'},
                    {'ActiveState': 'failed', 'Result': 'exit-code',
                     'ExecMainStatus': '1'}]), \
                patch.object(m.time, 'time', return_value=self.NOW):
            observation = m.collect(
                {'server': 'demo-1', 'services': [service]})['services'][
                    'demo-1:backup']
        self.assertEqual(observation['state'], 'down')
        self.assertNotIn('backup', observation)
        self.assertEqual(observation['transport']['state'], 'down')
        self.assertEqual(observation['transport']['error_code'], 'tunnel_closed')


class ControlledReceiptTests(unittest.TestCase):
    NOW = 1_700_000_000.0

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.receipt = Path(self.temp.name) / 'receipt.json'
        self.receipt.write_text('{"ok":true}', encoding='utf-8')

    def test_bounded_stable_regular_file_is_read_as_json_object(self):
        self.assertEqual(m.controlled_receipt(str(self.receipt)), {'ok': True})
        self.receipt.write_bytes(b'[' * (m.RECEIPT_MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            m.controlled_receipt(str(self.receipt))

    def test_non_regular_and_changed_files_are_rejected(self):
        with self.assertRaises((OSError, ValueError)):
            m.controlled_receipt(self.temp.name)
        original = m.os.fstat
        def changed(descriptor):
            value = original(descriptor)
            return type('Changed', (), {
                'st_dev': value.st_dev, 'st_ino': value.st_ino + 1,
                'st_size': value.st_size, 'st_mtime_ns': value.st_mtime_ns,
                'st_uid': value.st_uid, 'st_mode': value.st_mode})()
        with patch.object(m.os, 'fstat', side_effect=changed):
            with self.assertRaises(ValueError):
                m.controlled_receipt(str(self.receipt))

    @unittest.skipUnless(hasattr(m.os, 'symlink'), 'symlink unavailable')
    def test_symlink_is_rejected_when_supported(self):
        link = Path(self.temp.name) / 'receipt-link.json'
        try:
            link.symlink_to(self.receipt)
        except OSError as error:
            self.skipTest('symlink creation unavailable: ' + str(error))
        with self.assertRaises(ValueError):
            m.controlled_receipt(str(link))

    def test_posix_owner_and_mode_policy_are_enforced(self):
        def fake(uid, permissions):
            return type('Stat', (), {
                'st_dev': 1, 'st_ino': 2, 'st_size': 12,
                'st_mtime_ns': 3,
                'st_uid': uid, 'st_mode': m.stat.S_IFREG | permissions})()
        with patch.object(m.os, 'name', 'posix'):
            for permissions in (0o600, 0o644):
                m._validate_receipt_stat(
                    fake(0, permissions), m.BACKUP_RECEIPT_MODES)
            m._validate_receipt_stat(
                fake(0, 0o644), m.TRANSPORT_RECEIPT_MODES)
            for value, allowed in (
                    (fake(1000, 0o644), m.BACKUP_RECEIPT_MODES),
                    (fake(0, 0o666), m.BACKUP_RECEIPT_MODES),
                    (fake(0, 0o600), m.TRANSPORT_RECEIPT_MODES)):
                with self.subTest(mode=value.st_mode, uid=value.st_uid), \
                        self.assertRaises(ValueError):
                    m._validate_receipt_stat(value, allowed)

    def test_posix_backup_receipt_accepts_both_documented_modes(self):
        receipt = Path(self.temp.name) / 'backup.json'
        receipt.write_text(json.dumps({
            'status': 'success', 'profile': 'demo-1',
            'snapshot': 'snapshot-1', 'checked': True,
            'check_mode': 'read-data',
            'utc': datetime.fromtimestamp(
                self.NOW - 30, timezone.utc).isoformat()}), encoding='utf-8')

        def read_with_mode(permissions):
            original_lstat = m.os.lstat
            original_fstat = m.os.fstat

            def root_owned(result):
                return type('Stat', (), {
                    'st_dev': result.st_dev, 'st_ino': result.st_ino,
                    'st_size': result.st_size,
                    'st_mtime_ns': result.st_mtime_ns,
                    'st_uid': 0,
                    'st_mode': m.stat.S_IFREG | permissions})()

            with patch.object(m.os, 'name', 'posix'), \
                    patch.object(
                        m.os, 'lstat',
                        side_effect=lambda path: root_owned(
                            original_lstat(path))), \
                    patch.object(
                        m.os, 'fstat',
                        side_effect=lambda descriptor: root_owned(
                            original_fstat(descriptor))):
                return m.receipt_metadata(
                    {'receipt': str(receipt)}, 'demo-1', self.NOW)

        for permissions in (0o600, 0o644):
            with self.subTest(mode=permissions):
                self.assertEqual(
                    read_with_mode(permissions)['snapshot_id'],
                    'snapshot-1')

    def test_posix_transport_receipt_still_rejects_private_mode(self):
        transport = Path(self.temp.name) / 'transport.json'
        transport.write_text(json.dumps({
            'schema': 1, 'profile': 'demo-1', 'status': 'up',
            'checked_utc': datetime.fromtimestamp(
                self.NOW - 30, timezone.utc).isoformat(),
            'unauthenticated_status': 401,
            'authenticated_config_status': 'pinned',
            'config_sha256': 'a' * 64}), encoding='utf-8')
        original_lstat = m.os.lstat
        original_fstat = m.os.fstat

        def root_owned(result):
            return type('Stat', (), {
                'st_dev': result.st_dev, 'st_ino': result.st_ino,
                'st_size': result.st_size,
                'st_mtime_ns': result.st_mtime_ns,
                'st_uid': 0,
                'st_mode': m.stat.S_IFREG | 0o600})()

        with patch.object(m.os, 'name', 'posix'), \
                patch.object(
                    m.os, 'lstat',
                    side_effect=lambda path: root_owned(
                        original_lstat(path))), \
                patch.object(
                    m.os, 'fstat',
                    side_effect=lambda descriptor: root_owned(
                        original_fstat(descriptor))):
            value = m.transport_metadata(
                str(transport), 'demo-1', self.NOW)
        self.assertEqual(value['state'], 'unknown')
        self.assertEqual(value['error_code'], 'receipt_invalid')


class ServiceSchemaTests(unittest.TestCase):
    def setUp(self):
        self.systemd={'timer':'example-backup.timer','service':'example-backup.service',
                      'receipt':'/var/lib/example-backup/last-success.json'}

    def test_backup_job_is_exclusive_and_does_not_inject_empty_targets(self):
        self.assertEqual(schema.safe_collector_service(
            {'id':'demo:backup','backup_job':self.systemd}),
            {'id':'demo:backup','backup_job':{**self.systemd,'max_age_seconds':93600}})
        self.assertEqual(schema.safe_collector_service(
            {'id':'demo:backup','backup_job':{
                **self.systemd,'scheduler':'systemd'}}),
            {'id':'demo:backup','backup_job':{
                **self.systemd,'scheduler':'systemd',
                'max_age_seconds':93600}})
        self.assertEqual(schema.safe_collector_service(
            {'id':'demo:empty','containers':[],'units':[]}),{'id':'demo:empty'})
        self.assertEqual(schema.safe_collector_service({
            'id':'demo:backup','backup_job':self.systemd,
            'probe':{'url':'https://example.invalid/'}}),
            {'id':'demo:backup','backup_job':{
                **self.systemd,'max_age_seconds':93600}})
        for extra in ({'containers':['web']},{'units':['demo.service']},
                      {'timer':'other.timer'},{'process':'backup'}):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                schema.safe_collector_service({'id':'demo:backup','backup_job':self.systemd,**extra})

    def test_probe_schema_is_strict_and_exclusive(self):
        probe={'url':'https://status.example.invalid/health?full=1',
               'label':'Public health','codes':[200,204]}
        self.assertEqual(schema.safe_collector_service(
            {'id':'demo:probe','probe':probe}),{'id':'demo:probe'})
        self.assertEqual(schema.safe_probe({'url':'http://example.invalid/'}),
                         {'url':'http://example.invalid/'})
        invalid=[
            None,[],{},
            {'url':'ftp://example.invalid/'},
            {'url':'https://user:secret@example.invalid/'},
            {'url':'https:///missing-host'},
            {'url':'https://example.invalid:99999/'},
            {'url':'https://example.invalid/','label':''},
            {'url':'https://example.invalid/','label':'bad\nlabel'},
            {'url':'https://example.invalid/','label':'x'*81},
            {'url':'https://example.invalid/','codes':[]},
            {'url':'https://example.invalid/','codes':[True]},
            {'url':'https://example.invalid/','codes':[99]},
            {'url':'https://example.invalid/','codes':[200,200]},
            {'url':'https://example.invalid/','unexpected':True},
        ]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):
                schema.safe_probe(value)
        for extra,expected in (
                ({'containers':['web']},{'id':'demo:probe','containers':['web']}),
                ({'units':['demo.service']},{'id':'demo:probe','units':['demo.service']}),
                ({'timer':'other.timer'},{'id':'demo:probe','timer':'other.timer'}),
                ({'process':'probe-worker'},{'id':'demo:probe','process':'probe-worker'})):
            with self.subTest(extra=extra):
                self.assertEqual(
                    schema.safe_collector_service({'id':'demo:probe','probe':probe,**extra}),
                    expected)
        with self.assertRaises(ValueError):
            schema.safe_collector_service({'id':'demo:probe','probe':probe,
                                           'containers':['web'],'process':'worker'})

    def test_backup_job_validates_scheduler_fields_and_bounded_values(self):
        cases=[
            {**self.systemd,'scheduler':'unsupported'},
            {**self.systemd,'timer':'bad timer'},
            {**self.systemd,'receipt':'relative/last-success.json'},
            {**self.systemd,'receipt':'/var/lib/../secret.json'},
            {**self.systemd,'receipt':'/var/lib/example\x01/secret.json'},
            {**self.systemd,'receipt':'/var/lib/example\x7f/secret.json'},
            {**self.systemd,'max_age_seconds':float('inf')},
            {**self.systemd,'max_age_seconds':59},
            {**self.systemd,'unexpected':True},
        ]
        for job in cases:
            with self.subTest(job=job),self.assertRaises(ValueError):
                schema.safe_backup_job(job)
        launchd={'scheduler':'launchd','label':'com.example.backup',
                 'plist':'/Library/LaunchDaemons/com.example.backup.plist',
                 'receipt':'/Library/Application Support/Example/health.json'}
        self.assertNotIn('plist_sha256', schema.safe_backup_job(launchd))
        self.assertEqual(schema.safe_backup_job(
            {**launchd,'plist_sha256':'a'*64})['plist_sha256'],'a'*64)
        with self.assertRaises(ValueError):
            schema.safe_backup_job({**launchd,'plist_sha256':'A'*64})
        with self.assertRaises(ValueError):
            schema.safe_backup_job({**launchd,'plist':'/Library/\x1fExample.plist'})

    def test_every_active_monitor_combination_is_mutually_exclusive(self):
        backrest={'scheduler':'backrest','service':'example-backrest.service',
                  'config':'/opt/example/config.json','oplog':'/opt/example/oplog.sqlite',
                  'plan':'example-daily',
                  'receipt':'/var/lib/example/success.json'}
        launchd={'scheduler':'launchd','label':'com.example.backup',
                 'plist':'/Library/LaunchDaemons/com.example.backup.plist',
                 'plist_sha256':'a'*64,
                 'receipt':'/Library/Application Support/Example/health.json'}
        monitors=(self.systemd,backrest,launchd)
        extras=({'containers':['web']},{'units':['demo.service']},
                {'timer':'other.timer'},{'process':'worker'})
        for backup_job in monitors:
            for extra in extras:
                with self.subTest(backup_job=backup_job,extra=extra), \
                        self.assertRaises(ValueError):
                    schema.safe_collector_service({
                        'id':'demo:backup','backup_job':backup_job,**extra})
            with self.subTest(backup_job=backup_job):
                clean=schema.safe_collector_service({
                    'id':'demo:backup','backup_job':backup_job,
                    'probe':{'url':'https://example.invalid/'}})
                self.assertNotIn('probe',clean)
                self.assertEqual(clean['backup_job']['max_age_seconds'],93600)

    def test_registry_validates_identity_and_rejects_duplicates(self):
        registry={'servers':[{'id':'demo-1','name':'Demo','address':'example.invalid'}],
                  'services':[{'id':'demo-1:backup','server':'demo-1','name':'Backup',
                               'category':'ops','app':False,'href':None,
                               'backup_job':self.systemd}]}
        self.assertTrue(schema.validate_registry(registry))
        invalid=[
            {**registry,'servers':registry['servers']*2},
            {**registry,'services':registry['services']*2},
            {**registry,'services':[{**registry['services'][0],
                                     'server':'missing'}]},
            {**registry,'services':[{**registry['services'][0],'app':1}]},
        ]
        for value in invalid:
            with self.subTest(value=value),self.assertRaises(ValueError):
                schema.validate_registry(value)


class CollectorConfigurationTests(unittest.TestCase):
    def test_generated_scoped_config_preserves_backup_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'console').mkdir()
            (root / 'runtime/console').mkdir(parents=True)
            script = root / 'scripts/add_collector.py'
            shutil.copyfile(ROOT / 'scripts/add_collector.py', script)
            shutil.copyfile(ROOT / 'scripts/service_schema.py', root / 'scripts/service_schema.py')
            shutil.copyfile(ROOT / 'console/service_schema.py',
                            root / 'console/service_schema.py')
            job = {'timer': 'example-backup.timer', 'service': 'example-backup.service',
                   'receipt': '/var/lib/example-backup/last-success.json',
                   'max_age_seconds': 93600}
            registry = {'servers': [{'id': 'demo-1'}], 'services': [
                {'id': 'demo-1:backup', 'server': 'demo-1', 'name': 'Daily backup',
                 'backup_job': job,
                 'probe': {'url': 'https://status.example.invalid/health'}},
                {'id': 'demo-1:http', 'server': 'demo-1', 'name': 'HTTP only',
                 'probe': {'url': 'https://entry.example.invalid/'}},
                {'id': 'demo-1:container', 'server': 'demo-1', 'name': 'Container',
                 'containers': ['example']},
                {'id': 'other:backup', 'server': 'other', 'backup_job': job}]}
            (root / 'runtime/console/registry.json').write_text(json.dumps(registry))
            (root / 'runtime/console/tokens.json').write_text('{}')
            with patch.object(sys, 'argv', [str(script), 'demo-1', 'https://dashboard.example.com']), \
                    contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(script), run_name='__main__')
            config = json.loads((root / 'runtime/collector-demo-1.json').read_text())
            self.assertEqual(config['services'], [
                {'id': 'demo-1:backup', 'backup_job': job},
                {'id': 'demo-1:container', 'containers': ['example']},
            ])
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
        receipt = {'status': 'success', 'profile': 'demo-1', 'snapshot': 'snapshot-1',
                   'checked': True, 'check_mode': 'read-data',
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

    def test_paged_operation_scan_finds_backup_after_page_of_unrelated_records(self):
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.executemany(
                'INSERT INTO operations VALUES (?,?,?,?,?,?)',
                [(1000 + index, 1, 3, '', (self.NOW - index) * 1000,
                  b'\xd2\x06\x00') for index in range(300)])
            db.commit()
        self.assertEqual(m.BACKREST_PAGE_SIZE, 256)
        self.assertEqual(self.state(), 'up')

    def test_running_backup_preserves_last_checked_copy(self):
        self.add_operation(2, 2, snapshot='')
        self.assertEqual(self.state(), 'maintenance')

    def test_running_backup_requires_a_fresh_valid_previous_copy(self):
        self.add_operation(2, 2, snapshot='')
        self.write_receipt(age=93601)
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.execute('UPDATE operations SET start_time_ms=? WHERE id=1',
                       ((self.NOW-93700)*1000,))
            db.commit()
        self.assertEqual(self.state(), 'down')
        self.write_receipt(**{'check_mode': None})
        self.assertEqual(self.state(), 'unknown')
        self.write_receipt(**{'check_mode': 'metadata'})
        self.assertEqual(self.state(), 'down')
        self.write_receipt(**{'status': 'failed'})
        self.assertEqual(self.state(), 'down')

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
        cases = [
            ({'snapshot': 'old'}, 'unknown'),
            ({'checked': False}, 'down'),
            ({'checked': 0}, 'unknown'),
            ({'checked': 1}, 'unknown'),
            ({'checked': 'true'}, 'unknown'),
            ({'profile': 'other'}, 'unknown'),
            ({'status': 'failed'}, 'down'),
            ({'check_mode': None}, 'unknown'),
            ({'check_mode': 1}, 'unknown'),
            ({'check_mode': ''}, 'unknown'),
            ({'check_mode': 'metadata'}, 'down'),
        ]
        for changes, expected in cases:
            self.write_receipt(**changes)
            self.assertEqual(self.state(), expected)

    def test_expired_copy_is_down_future_unknown(self):
        with closing(sqlite3.connect(self.job['oplog'])) as db:
            db.execute('UPDATE operations SET start_time_ms=?', ((self.NOW-94000)*1000,))
            db.commit()
        self.write_receipt(age=93601)
        self.assertEqual(self.state(), 'down')
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
