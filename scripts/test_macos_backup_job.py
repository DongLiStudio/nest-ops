"""Offline checks for the macOS backup_job collector contract.

Ruby is not part of the Windows development image. The Python suite therefore
always runs static contract checks and runs the Ruby/Minitest fixture whenever
Ruby is available.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "collectors" / "macos" / "collector.rb"
RUBY_TEST = ROOT / "scripts" / "test_macos_backup_job.rb"


class MacOSBackupJobContractTests(unittest.TestCase):
    def test_collector_contract_is_native_bounded_and_metadata_only(self):
        source = COLLECTOR.read_text(encoding="utf-8")
        for token in (
            "def valid_launchd_job",
            "def controlled_public_file",
            "def safe_backup_arguments",
            "def launchd_schedule",
            "def backup_state",
            "def backup_metadata",
            "BACKUP_ID = /\\A[a-f0-9]{64}\\z/",
            "LAUNCHD_KEYS",
            "working_directory == '/Library/Restic'",
            "max_age >= 60 && max_age <= 604_800",
            "receipt['status'] == 'running' ? 'maintenance' : 'up'",
            "invalid = {'checked_at' => now.to_f, 'state' => 'unknown'",
            "return invalid unless",
            "transport = transport_metadata(service['backup_job']['transport_receipt'],",
            "item['backup'] = metadata if metadata",
            "item['transport'] = transport if transport",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        for forbidden in ("backup_job['command']", "Open3.capture3(job", "receipt.each"):
            self.assertNotIn(forbidden, source)

    @unittest.skipUnless(shutil.which("ruby"), "Ruby runtime is unavailable")
    def test_ruby_syntax_and_offline_state_fixture(self):
        syntax = subprocess.run(
            [shutil.which("ruby"), "-c", str(COLLECTOR)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        fixture = subprocess.run(
            [shutil.which("ruby"), str(RUBY_TEST)],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(fixture.returncode, 0, fixture.stdout + fixture.stderr)


if __name__ == "__main__":
    unittest.main()
