#!/usr/bin/env python3
"""Guarded Chrome profile cleanup utility for web-uplift evidence sweeps.

Sweeps `/tmp/web-uplift-cdp-*` profile directories left behind by CLI invocations,
enforcing strict safety guards:
1. Target validation: strictly matches `web-uplift-cdp-[A-Za-z0-9_-]+`, must be a
   real directory directly beneath the target tmp directory, never a symlink.
2. User ownership: only removes directories owned by the executing user.
3. Process guard: scans `/proc/*/cmdline` (with ps fallback) for any running Chrome
   or Node process referencing the profile path/name. Any active profile is never deleted.
4. Age guard: skips directories modified or created within the last N minutes
   (default 15 minutes) to protect active runs between `mkdtemp` and process launch.
5. Dry-run mode: `--dry-run` reports inspectable candidates without modifying disk.

Usage:
    python3 scripts/clean_stale_profiles.py [--dry-run] [--min-age-minutes 15] [--json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROFILE_PATTERN = re.compile(r"^web-uplift-cdp-[A-Za-z0-9_-]+$")
REF_PATTERN = re.compile(r"web-uplift-cdp-[A-Za-z0-9_-]+")


def get_live_profile_references() -> set[str]:
    """Scan running processes for arguments referencing web-uplift-cdp profiles."""
    active_refs: set[str] = set()
    current_pid = str(os.getpid())

    # Primary fast-path: Linux /proc filesystem
    proc_paths = glob.glob("/proc/[0-9]*/cmdline")
    if proc_paths:
        for cmd_path in proc_paths:
            pid = cmd_path.split("/")[2]
            if pid == current_pid:
                continue
            try:
                with open(cmd_path, "rb") as handle:
                    raw = handle.read().decode("utf-8", errors="replace")
                    for match in REF_PATTERN.findall(raw):
                        active_refs.add(match)
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
        return active_refs

    # Fallback: ps -eo pid,args
    try:
        proc = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5)
        for line in proc.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            pid = parts[0]
            if pid == current_pid:
                continue
            args = parts[1] if len(parts) > 1 else ""
            for match in REF_PATTERN.findall(args):
                active_refs.add(match)
    except Exception:
        pass

    return active_refs


def compute_dir_size(path: Path) -> int:
    """Recursively calculate directory size in bytes without following symlinks."""
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += compute_dir_size(Path(entry.path))
            except (PermissionError, FileNotFoundError):
                continue
    except (PermissionError, FileNotFoundError):
        pass
    return total


def sweep_stale_profiles(
    tmp_dir: Path = Path("/tmp"),
    min_age_seconds: int = 900,
    dry_run: bool = False,
    live_refs_override: set[str] | None = None,
) -> dict[str, Any]:
    """Scan and clean stale profiles according to age and process safety guards."""
    tmp_dir = tmp_dir.resolve()
    live_refs = live_refs_override if live_refs_override is not None else get_live_profile_references()
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    now = time.time()

    candidates: list[Path] = []
    if tmp_dir.exists():
        for item in tmp_dir.iterdir():
            if PROFILE_PATTERN.match(item.name):
                candidates.append(item)

    candidates.sort(key=lambda p: p.name)

    kept_active: list[dict[str, Any]] = []
    kept_recent: list[dict[str, Any]] = []
    skipped_invalid: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    bytes_reclaimed = 0

    for path in candidates:
        name = path.name

        # Safety Guard 1: Must be a directory, not a symlink, directly in tmp_dir
        try:
            is_link = path.is_symlink()
            is_dir = path.is_dir()
            if is_link or not is_dir or path.parent.resolve() != tmp_dir:
                skipped_invalid.append({"path": str(path), "reason": "not a direct directory or is a symlink"})
                continue
        except (PermissionError, FileNotFoundError):
            continue

        # Safety Guard 2: Must be owned by current user (if user check available)
        try:
            stat_res = path.stat(follow_symlinks=False)
            if current_uid is not None and stat_res.st_uid != current_uid:
                skipped_invalid.append({"path": str(path), "reason": f"not owned by current user (uid {stat_res.st_uid})"})
                continue
        except (PermissionError, FileNotFoundError):
            continue

        # Safety Guard 3: Process Guard (cannot delete if referenced by any live process)
        if name in live_refs or str(path) in live_refs:
            kept_active.append({
                "path": str(path),
                "name": name,
                "reason": "in use by live process",
            })
            continue

        # Safety Guard 4: Age Guard (must be older than min_age_seconds)
        dir_time = stat_res.st_mtime
        age_seconds = now - dir_time
        if age_seconds < min_age_seconds:
            kept_recent.append({
                "path": str(path),
                "name": name,
                "ageSeconds": round(age_seconds, 1),
                "reason": f"modified within last {min_age_seconds}s",
            })
            continue

        # Candidate is safe to remove
        dir_size = compute_dir_size(path)
        if dry_run:
            removed.append({
                "path": str(path),
                "name": name,
                "sizeBytes": dir_size,
                "action": "would_remove",
            })
            bytes_reclaimed += dir_size
        else:
            try:
                shutil.rmtree(path)
                removed.append({
                    "path": str(path),
                    "name": name,
                    "sizeBytes": dir_size,
                    "action": "removed",
                })
                bytes_reclaimed += dir_size
            except Exception as exc:
                errors.append({
                    "path": str(path),
                    "error": f"{type(exc).__name__}: {exc}",
                })

    return {
        "tmpDir": str(tmp_dir),
        "minAgeSeconds": min_age_seconds,
        "dryRun": dry_run,
        "scanned": len(candidates),
        "removedCount": len(removed),
        "bytesReclaimed": bytes_reclaimed,
        "mbReclaimed": round(bytes_reclaimed / (1024 * 1024), 2),
        "keptActiveCount": len(kept_active),
        "keptRecentCount": len(kept_recent),
        "skippedInvalidCount": len(skipped_invalid),
        "errorCount": len(errors),
        "removed": removed,
        "keptActive": kept_active,
        "keptRecent": kept_recent,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep stale Chrome profile directories in /tmp")
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp"), help="Target directory (default /tmp)")
    parser.add_argument("--min-age-minutes", type=float, default=15.0, help="Minimum age in minutes (default 15)")
    parser.add_argument("--min-age-seconds", type=int, default=None, help="Minimum age in seconds override")
    parser.add_argument("--dry-run", action="store_true", help="Report candidates without deleting")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--verbose", action="store_true", help="Print details for each profile")
    args = parser.parse_args(argv)

    min_age_seconds = args.min_age_seconds if args.min_age_seconds is not None else int(args.min_age_minutes * 60)
    summary = sweep_stale_profiles(tmp_dir=args.tmp_dir, min_age_seconds=min_age_seconds, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if summary["errorCount"] == 0 else 1

    action_label = "Would remove" if args.dry_run else "Removed"
    print(f"Profile cleanup summary ({'DRY RUN' if args.dry_run else 'APPLIED'}):")
    print(f"  Target directory: {summary['tmpDir']}")
    print(f"  Age threshold:    >= {min_age_seconds}s ({round(min_age_seconds/60, 1)}m)")
    print(f"  Scanned profiles: {summary['scanned']}")
    print(f"  Kept (in use):    {summary['keptActiveCount']}")
    print(f"  Kept (too recent):{summary['keptRecentCount']}")
    print(f"  {action_label}:    {summary['removedCount']} ({summary['mbReclaimed']} MB)")
    if summary["errorCount"] > 0:
        print(f"  Errors:           {summary['errorCount']}")

    if args.verbose:
        if summary["keptActive"]:
            print("\nIn-use profiles:")
            for p in summary["keptActive"]:
                print(f"  - {p['name']} ({p['reason']})")
        if summary["removed"]:
            print(f"\n{action_label} profiles:")
            for p in summary["removed"]:
                print(f"  - {p['name']} ({p['sizeBytes'] / 1024:.1f} KB)")

    return 0 if summary["errorCount"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
