"""Compatibility entry point for the canonical console service schema."""
import importlib.util
from pathlib import Path

_CANONICAL = Path(__file__).resolve().parents[1] / 'console' / 'service_schema.py'
_SPEC = importlib.util.spec_from_file_location('nestops_service_schema', _CANONICAL)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError('cannot load console/service_schema.py')
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

for _name in ('safe_backup_job', 'safe_probe', 'safe_collector_service',
              'validate_registry'):
    globals()[_name] = getattr(_MODULE, _name)

__all__ = ['safe_backup_job', 'safe_probe', 'safe_collector_service',
           'validate_registry']
