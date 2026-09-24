#!/usr/bin/env python3
"""Tests for guarded Chrome profile cleanup utility (clean_stale_profiles.py)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import clean_stale_profiles


class StaleProfileCleanerTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="cleaner-test-tmp-"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def create_mock_profile(self, name: str, age_seconds: float = 0, is_file: bool = False, is_symlink: bool = False) -> Path:
        target = self.tmp_dir / name
        if is_symlink:
            real_dir = self.tmp_dir / ("real-target-for-" + name)
            real_dir.mkdir(parents=True, exist_ok=True)
            target.symlink_to(real_dir)
        elif is_file:
            target.write_text("dummy")
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / "Default").mkdir(parents=True, exist_ok=True)
            (target / "Default" / "Preferences").write_text('{"mock": true}')

        if age_seconds > 0:
            past_time = time.time() - age_seconds
            os.utime(target, (past_time, past_time))
        return target

    def test_stale_profile_is_removed_when_older_than_threshold_and_not_in_use(self):
        profile = self.create_mock_profile("web-uplift-cdp-Stale123", age_seconds=1200)
        summary = clean_stale_profiles.sweep_stale_profiles(
            tmp_dir=self.tmp_dir,
            min_age_seconds=900,
            dry_run=False,
            live_refs_override=set(),
        )
        self.assertEqual(summary["removedCount"], 1)
        self.assertEqual(summary["keptActiveCount"], 0)
        self.assertEqual(summary["keptRecentCount"], 0)
        self.assertFalse(profile.exists(), "stale profile was not removed")

    def test_recent_profile_is_kept_by_age_guard(self):
        profile = self.create_mock_profile("web-uplift-cdp-Recent456", age_seconds=60)
        summary = clean_stale_profiles.sweep_stale_profiles(
            tmp_dir=self.tmp_dir,
            min_age_seconds=900,
            dry_run=False,
            live_refs_override=set(),
        )
        self.assertEqual(summary["removedCount"], 0)
        self.assertEqual(summary["keptRecentCount"], 1)
        self.assertTrue(profile.exists(), "recent profile was prematurely deleted")

    def test_active_profile_is_kept_by_process_guard(self):
        profile = self.create_mock_profile("web-uplift-cdp-Active789", age_seconds=1500)
        # Simulate that "web-uplift-cdp-Active789" is in a running process command line
        summary = clean_stale_profiles.sweep_stale_profiles(
            tmp_dir=self.tmp_dir,
            min_age_seconds=900,
            dry_run=False,
            live_refs_override={"web-uplift-cdp-Active789"},
        )
        self.assertEqual(summary["removedCount"], 0)
        self.assertEqual(summary["keptActiveCount"], 1)
        self.assertEqual(summary["keptActive"][0]["name"], "web-uplift-cdp-Active789")
        self.assertTrue(profile.exists(), "active profile was deleted while in use")

    def test_dry_run_reports_candidates_without_deleting(self):
        profile = self.create_mock_profile("web-uplift-cdp-DryRunTest", age_seconds=1200)
        summary = clean_stale_profiles.sweep_stale_profiles(
            tmp_dir=self.tmp_dir,
            min_age_seconds=900,
            dry_run=True,
            live_refs_override=set(),
        )
        self.assertEqual(summary["removedCount"], 1)
        self.assertEqual(summary["removed"][0]["action"], "would_remove")
        self.assertTrue(profile.exists(), "dry_run mode must not delete files")

    def test_symlinks_and_non_matching_dirs_are_skipped(self):
        symlink = self.create_mock_profile("web-uplift-cdp-Symlink", age_seconds=1200, is_symlink=True)
        plain_file = self.create_mock_profile("web-uplift-cdp-File", age_seconds=1200, is_file=True)
        other_dir = self.create_mock_profile("other-random-dir", age_seconds=1200)

        summary = clean_stale_profiles.sweep_stale_profiles(
            tmp_dir=self.tmp_dir,
            min_age_seconds=900,
            dry_run=False,
            live_refs_override=set(),
        )
        self.assertEqual(summary["removedCount"], 0)
        self.assertTrue(symlink.exists())
        self.assertTrue(plain_file.exists())
        self.assertTrue(other_dir.exists())

    def test_cli_json_output(self):
        self.create_mock_profile("web-uplift-cdp-CliJson", age_seconds=1200)
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "clean_stale_profiles.py"),
            "--tmp-dir", str(self.tmp_dir),
            "--min-age-seconds", "600",
            "--dry-run",
            "--json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
        self.assertTrue(data["dryRun"])
        self.assertEqual(data["scanned"], 1)
        self.assertEqual(data["removedCount"], 1)
        self.assertEqual(data["removed"][0]["name"], "web-uplift-cdp-CliJson")

    def test_live_process_scanner_finds_running_process(self):
        """Start a dummy process holding a mock profile name and verify live_refs catches it."""
        mock_name = "web-uplift-cdp-RunningProcessTest"
        proc = subprocess.Popen(["bash", "-c", "sleep 10; exit 0", f"--user-data-dir=/tmp/{mock_name}"])
        try:
            live_refs = clean_stale_profiles.get_live_profile_references()
            self.assertIn(mock_name, live_refs, f"live process scanner missed {mock_name} in running process args")
        finally:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
