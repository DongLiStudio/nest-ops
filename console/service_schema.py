"""Validation for collector service entries and their private check targets."""
import math
import re
from urllib.parse import urlsplit

SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.@:+-]{0,127}$')
SAFE_PATH = re.compile(r'^/[^\x00-\x1f\x7f]{1,1023}$')
SHA256 = re.compile(r'^[0-9a-f]{64}$')

COMMON_BACKUP = {'receipt', 'transport_receipt', 'max_age_seconds'}
PROBE_KEYS = {'url', 'label', 'codes'}
BACKUP_KEYS = {
    'systemd': COMMON_BACKUP | {'scheduler', 'timer', 'service'},
    'backrest': COMMON_BACKUP | {'scheduler', 'service', 'config', 'oplog', 'plan'},
    'launchd': COMMON_BACKUP | {'scheduler', 'label', 'plist', 'plist_sha256'},
}


def _safe_name(value):
    return isinstance(value, str) and bool(SAFE_NAME.fullmatch(value))


def _safe_path(value):
    return (isinstance(value, str) and bool(SAFE_PATH.fullmatch(value)) and
            all(part not in ('', '.', '..') for part in value.split('/')[1:]))


def safe_backup_job(value):
    if not isinstance(value, dict):
        raise ValueError('backup_job must be an object')
    scheduler = value.get('scheduler', 'systemd')
    if scheduler not in BACKUP_KEYS:
        raise ValueError('unsupported backup scheduler')
    unknown = set(value) - BACKUP_KEYS[scheduler]
    if unknown:
        raise ValueError('unsupported backup_job fields: ' + ', '.join(sorted(unknown)))
    required = {
        'systemd': ('timer', 'service', 'receipt'),
        'backrest': ('service', 'config', 'oplog', 'plan', 'receipt'),
    'launchd': ('label', 'plist', 'receipt'),
    }[scheduler]
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
        raise ValueError('missing backup_job fields')
    for key in ('timer', 'service', 'label', 'plan'):
        if key in value and not _safe_name(value[key]):
            raise ValueError('invalid backup_job ' + key)
    for key in ('config', 'oplog', 'plist', 'receipt', 'transport_receipt'):
        if key in value and not _safe_path(value[key]):
            raise ValueError('invalid backup_job path')
    if 'plist_sha256' in value and (
            not isinstance(value['plist_sha256'], str) or
            not SHA256.fullmatch(value['plist_sha256'])):
        raise ValueError('invalid backup_job plist_sha256')
    max_age = value.get('max_age_seconds', 93600)
    if (isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or
            not math.isfinite(max_age) or not 60 <= max_age <= 604800):
        raise ValueError('invalid backup_job max_age_seconds')
    clean = dict(value)
    clean.setdefault('max_age_seconds', 93600)
    return clean


def safe_probe(value):
    """Validate a server-side HTTP monitor without trusting private URLs in output."""
    if not isinstance(value, dict):
        raise ValueError('probe must be an object')
    unknown = set(value) - PROBE_KEYS
    if unknown:
        raise ValueError('unsupported probe fields: ' + ', '.join(sorted(unknown)))
    url = value.get('url')
    if not isinstance(url, str) or not url.strip():
        raise ValueError('invalid probe url')
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as error:
        raise ValueError('invalid probe url') from error
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None):
        raise ValueError('invalid probe url')
    clean = {'url': url}
    if 'label' in value:
        label = value['label']
        if (not isinstance(label, str) or not label.strip() or len(label) > 80 or
                any(ord(char) < 32 or ord(char) == 127 for char in label)):
            raise ValueError('invalid probe label')
        clean['label'] = label
    if 'codes' in value:
        codes = value['codes']
        if (not isinstance(codes, list) or not codes or
                any(isinstance(code, bool) or not isinstance(code, int) or
                    not 100 <= code <= 599 for code in codes) or
                len(set(codes)) != len(codes)):
            raise ValueError('invalid probe codes')
        clean['codes'] = list(codes)
    return clean


def safe_collector_service(service):
    """Resolve one registry service into a strict remote-check allowlist."""
    if not isinstance(service, dict) or not _safe_name(service.get('id')):
        raise ValueError('invalid service id')
    result = {'id': service['id']}
    probe = safe_probe(service['probe']) if 'probe' in service else None
    for key in ('containers', 'units'):
        value = service.get(key, [])
        if not isinstance(value, list) or any(not _safe_name(item) for item in value):
            raise ValueError('invalid ' + key)
        if len(set(value)) != len(value):
            raise ValueError('duplicate ' + key)
        if key in service and value:
            result[key] = list(value)
    for key in ('timer', 'process'):
        if key in service:
            if not _safe_name(service[key]):
                raise ValueError('invalid ' + key)
            result[key] = service[key]
    if 'backup_job' in service:
        result['backup_job'] = safe_backup_job(service['backup_job'])

    # The HTTP probe is evaluated by the central console. A source collector
    # can continue to observe one local target on the same registry service.
    # This preserves the long-standing production shape where an entry probe
    # and its backing container or unit are recorded together. The probe is
    # intentionally omitted from the generated host-side collector config.
    local_targets = (
        bool(result.get('containers')),
        bool(result.get('units')),
        bool(result.get('timer')),
        bool(result.get('process')),
        'backup_job' in result,
    )
    if sum(local_targets) > 1:
        raise ValueError('multiple active local monitors')
    return result


def validate_registry(registry):
    if not isinstance(registry, dict):
        raise ValueError('registry must be an object')
    raw_servers = registry.get('servers', [])
    raw_services = registry.get('services', [])
    if not isinstance(raw_servers, list) or not isinstance(raw_services, list):
        raise ValueError('servers and services must be arrays')
    servers = set()
    for server in raw_servers:
        if not isinstance(server, dict) or not _safe_name(server.get('id')):
            raise ValueError('invalid server id')
        for key in ('name', 'address'):
            if not isinstance(server.get(key), str) or not server[key].strip():
                raise ValueError('invalid server ' + key)
        if server['id'] in servers:
            raise ValueError('duplicate server id')
        servers.add(server['id'])
    if len(servers) != len(raw_servers):
        raise ValueError('invalid or duplicate server id')
    seen = set()
    for service in raw_services:
        if not isinstance(service, dict):
            raise ValueError('invalid service')
        for key in ('name', 'category'):
            if not isinstance(service.get(key), str) or not service[key].strip():
                raise ValueError('invalid service ' + key)
        if not isinstance(service.get('app'), bool):
            raise ValueError('invalid service app')
        href = service.get('href')
        if href is not None and (not isinstance(href, str) or not href.strip()):
            raise ValueError('invalid service href')
        if service.get('server') not in servers:
            raise ValueError('service references unknown server')
        safe_collector_service(service)
        if service['id'] in seen:
            raise ValueError('duplicate service id')
        seen.add(service['id'])
    return True
