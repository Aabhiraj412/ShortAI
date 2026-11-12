"""Dry-run-first, reversible media retention and quarantine utility."""

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "backend" / "data" / "media_retention_policy.json"
MEDIA_ROOT = ROOT / "media"
PRODUCTIONS_ROOT = MEDIA_ROOT / "productions"
PROGRESS_PATH = ROOT / "backend" / "data" / "production_progress_store.json"
DATABASE_PATH = ROOT / "backend" / "studio.db"
ACTIVE_STATUSES = {"running", "in_progress", "active", "starting", "retrying"}
REGENERATABLE_SUFFIXES = {".mp4"}


def load_policy() -> dict[str, Any]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def ensure_no_symlink(path: Path) -> None:
    current = path
    while current != ROOT and ROOT in current.parents:
        if current.is_symlink():
            raise ValueError(f"Symlink path is not allowed: {relative(current)}")
        current = current.parent
    if path.is_symlink():
        raise ValueError(f"Symlink path is not allowed: {relative(path)}")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for base, dirs, names in os.walk(directory, followlinks=False):
        base_path = Path(base)
        safe_dirs = []
        for name in dirs:
            path = base_path / name
            if path.is_symlink():
                raise ValueError(f"Symlink directory is not allowed: {relative(path)}")
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in names:
            path = base_path / name
            if path.is_symlink():
                raise ValueError(f"Symlink file is not allowed: {relative(path)}")
            if path.is_file():
                files.append(path)
    return files


def storage_report() -> dict[str, Any]:
    directories = [
        "media/productions",
        "media/video",
        "media/thumbnail",
        "media/thumbnails",
        "media/audio",
        "media/subtitles",
        "media/quarantine",
    ]
    report: dict[str, Any] = {}
    for item in directories:
        path = ROOT / Path(item)
        files = walk_files(path) if path.is_dir() else []
        report[item] = {
            "exists": path.is_dir(),
            "file_count": len(files),
            "size_bytes": sum(file.stat().st_size for file in files),
        }
    return report


def disk_report() -> dict[str, int]:
    usage = shutil.disk_usage(ROOT)
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def duplicate_groups(files: list[Path]) -> list[dict[str, Any]]:
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        by_hash[file_hash(path)].append(path)
    return [
        {
            "sha256": digest,
            "paths": [relative(path) for path in paths],
            "size_bytes": paths[0].stat().st_size,
        }
        for digest, paths in by_hash.items()
        if len(paths) > 1
    ]


def repository_references(
    targets: list[Path],
    excluded_directories: list[Path] | None = None,
) -> dict[str, list[str]]:
    references = {relative(path): [] for path in targets}
    unique_names = unique_production_basenames()
    needles = {
        rel: {
            rel.lower(),
            str(path.resolve()).lower().replace("\\", "/"),
            *(
                {path.name.lower()}
                if path.name.lower() in unique_names
                else set()
            ),
        }
        for rel, path in zip(references, targets)
    }
    text_suffixes = {
        ".py", ".md", ".json", ".jsonl", ".txt", ".html", ".js", ".ts",
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv",
    }
    excluded = [directory.resolve() for directory in excluded_directories or []]
    for base in (ROOT / "backend", ROOT / "docs", PRODUCTIONS_ROOT):
        if not base.is_dir():
            continue
        for directory, dirs, names in os.walk(base, followlinks=False):
            current = Path(directory)
            resolved_current = current.resolve()
            if any(
                excluded_dir == resolved_current or excluded_dir in resolved_current.parents
                for excluded_dir in excluded
            ):
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if not (current / name).is_symlink()]
            for name in names:
                file_path = current / name
                if (
                    file_path.suffix.lower() not in text_suffixes
                    or file_path.is_symlink()
                    or file_path.name.startswith("PHASE_5_STAGE_3_21S_")
                ):
                    continue
                try:
                    if file_path.stat().st_size > 5_000_000:
                        continue
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                normalized = content.replace("\\", "/").lower()
                for rel, patterns in needles.items():
                    if any(pattern in normalized for pattern in patterns):
                        references[rel].append(relative(file_path))
    return references


def active_run_ids() -> set[str]:
    active: set[str] = set()
    if PROGRESS_PATH.is_file():
        try:
            data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
            productions = data.get("productions", {})
            for run_id, record in productions.items():
                if str(record.get("overall_status", "")).lower() in ACTIVE_STATUSES:
                    active.add(run_id)
        except (OSError, json.JSONDecodeError, AttributeError):
            raise RuntimeError("Could not verify production progress state; refusing cleanup.")
    if DATABASE_PATH.is_file():
        connection = sqlite3.connect(
            f"file:{DATABASE_PATH.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "persistent_jobs" in tables:
                rows = connection.execute(
                    "SELECT id, status FROM persistent_jobs"
                ).fetchall()
                active.update(
                    str(job_id)
                    for job_id, status in rows
                    if str(status or "").lower() in ACTIVE_STATUSES
                )
        finally:
            connection.close()
    return active


def unique_production_basenames() -> set[str]:
    counts: dict[str, int] = defaultdict(int)
    if PRODUCTIONS_ROOT.is_dir():
        for path in walk_files(PRODUCTIONS_ROOT):
            counts[path.name.lower()] += 1
    return {name for name, count in counts.items() if count == 1}


def database_references(targets: list[Path]) -> dict[str, list[str]]:
    found = {relative(path): [] for path in targets}
    if not DATABASE_PATH.is_file():
        return found
    needles = {
        rel: {
            rel.lower(),
            str((ROOT / Path(rel)).resolve()).lower().replace("\\", "/"),
        }
        for rel in found
    }
    connection = sqlite3.connect(
        f"file:{DATABASE_PATH.as_posix()}?mode=ro",
        uri=True,
    )
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if row[0] not in {"studio_settings", "sqlite_sequence"}
        ]
        for table in tables:
            quoted_table = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            text_columns = [
                row[1]
                for row in columns
                if any(kind in str(row[2]).upper() for kind in ("TEXT", "CHAR", "JSON"))
            ]
            if not text_columns:
                continue
            selected = ", ".join(
                '"' + name.replace('"', '""') + '"' for name in text_columns
            )
            for record in connection.execute(f"SELECT {selected} FROM {quoted_table}"):
                for column, value in zip(text_columns, record):
                    if not isinstance(value, str):
                        continue
                    normalized = value.replace("\\", "/").lower()
                    for rel, patterns in needles.items():
                        if any(pattern in normalized for pattern in patterns):
                            found[rel].append(f"{table}.{column}")
    finally:
        connection.close()
    return found


def valid_work_directory(path: Path, policy: dict[str, Any]) -> str:
    ensure_no_symlink(path)
    resolved = path.resolve(strict=True)
    if PRODUCTIONS_ROOT.resolve() not in resolved.parents:
        raise ValueError("Only paths below media/productions may be quarantined.")
    if path.name != policy["allowed_quarantine_subdirectory"]:
        raise ValueError("Only a render_work directory may be quarantined.")
    run_id = path.parent.name
    if any(fnmatch.fnmatchcase(run_id, pattern) for pattern in policy["protected_run_patterns"]):
        raise ValueError(f"Protected production run cannot be quarantined: {run_id}")
    if not path.is_dir():
        raise ValueError(f"Quarantine target is not a directory: {relative(path)}")
    if not (path.parent / "production_manifest.json").is_file():
        raise ValueError("Run production manifest is missing; classification is unknown.")
    if not (path.parent / "assets").is_dir() or not (path.parent / "narration").is_dir():
        raise ValueError("Reusable assets/narration are missing; regeneration is unverified.")
    video_dir = path.parent / "video"
    if not video_dir.is_dir() or not any(
        file.suffix.lower() == ".mp4" for file in walk_files(video_dir)
    ):
        raise ValueError("Completed run video is missing; refusing intermediate quarantine.")
    return run_id


def candidate_report(
    paths: list[Path],
    policy: dict[str, Any],
    override_retention: bool = False,
) -> dict[str, Any]:
    active = active_run_ids()
    files_by_dir: dict[str, list[Path]] = {}
    for directory in paths:
        run_id = valid_work_directory(directory, policy)
        if run_id in active:
            raise ValueError(f"Active production cannot be quarantined: {run_id}")
        files = walk_files(directory)
        if not files:
            raise ValueError(f"Empty render_work directory is not a candidate: {relative(directory)}")
        unexpected = [file for file in files if file.suffix.lower() not in REGENERATABLE_SUFFIXES and file.name != "concat_list.txt"]
        if unexpected:
            raise ValueError(
                "Render-work contains non-regeneratable/unknown files: "
                + ", ".join(relative(path) for path in unexpected)
            )
        retention_days = policy["retention_days"]["regeneratable_intermediates"]
        cutoff = datetime.now().timestamp() - float(retention_days) * 86400
        if not override_retention and any(
            file.stat().st_mtime > cutoff for file in files
        ):
            raise ValueError(
                f"Retention period ({retention_days} days) has not elapsed for "
                f"{relative(directory)}; use --override-retention only for reversible quarantine."
            )
        files_by_dir[relative(directory)] = files
    all_files = [file for files in files_by_dir.values() for file in files]
    references = database_references(all_files)
    referenced = {path: sources for path, sources in references.items() if sources}
    if referenced:
        raise ValueError(f"Database references prevent quarantine: {referenced}")
    code_refs = repository_references(
        paths + all_files,
        excluded_directories=paths,
    )
    external_refs = {
        path: sources for path, sources in code_refs.items() if sources
    }
    if external_refs:
        raise ValueError(f"Repository references prevent quarantine: {external_refs}")
    bytes_total = sum(file.stat().st_size for file in all_files)
    return {
        "directories": {
            directory: {
                "file_count": len(files),
                "size_bytes": sum(path.stat().st_size for path in files),
                "files": [
                    {
                        "path": relative(path),
                        "size_bytes": path.stat().st_size,
                        "sha256": file_hash(path),
                        "modified_local": datetime.fromtimestamp(
                            path.stat().st_mtime
                        ).astimezone().isoformat(),
                    }
                    for path in files
                ],
            }
            for directory, files in files_by_dir.items()
        },
        "file_count": len(all_files),
        "size_bytes": bytes_total,
        "reclaimable_disk_bytes": 0,
        "active_productions": sorted(active),
        "database_references": "none found for selected paths",
        "application_and_report_references": "none found for selected paths",
        "retention_override": override_retention,
        "note": "Quarantine is reversible and does not free disk space.",
    }


def command_plan(args: argparse.Namespace, policy: dict[str, Any]) -> None:
    report = storage_report()
    candidates: list[dict[str, Any]] = []
    for path in sorted(PRODUCTIONS_ROOT.glob("*/render_work")):
        try:
            plan = candidate_report([path], policy)
            candidates.append({
                "path": relative(path),
                "eligible": True,
                "file_count": plan["file_count"],
                "size_bytes": plan["size_bytes"],
            })
        except (ValueError, OSError, RuntimeError) as exc:
            candidates.append({
                "path": relative(path),
                "eligible": False,
                "reason": str(exc),
            })
    production_files = walk_files(PRODUCTIONS_ROOT)
    print(json.dumps({
        "mode": "dry_run",
        "disk": disk_report(),
        "storage": report,
        "exact_hash_duplicates": duplicate_groups(production_files),
        "potential_work_directories": candidates,
        "automatic_deletion": False,
    }, indent=2))


def command_quarantine(args: argparse.Namespace, policy: dict[str, Any]) -> None:
    directories = [ROOT / Path(item) for item in args.paths]
    plan = candidate_report(
        directories,
        policy,
        override_retention=args.override_retention,
    )
    print(json.dumps({"mode": "dry_run" if not args.confirm else "quarantine", **plan}, indent=2))
    if not args.confirm:
        return
    if not args.worker_idle_confirmed:
        raise ValueError("Pass --worker-idle-confirmed only after independently confirming no worker is active.")
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    batch = ROOT / Path(policy["quarantine_root"]) / stamp
    batch.mkdir(parents=True, exist_ok=False)
    manifest = {
        "policy_version": policy["policy_version"],
        "created_at": datetime.now().astimezone().isoformat(),
        "action": "quarantine",
        "dry_run": False,
        "retention_override": args.override_retention,
        "reason": "Completed validation-run render intermediates; source assets and final deliverables remain in place.",
        "files": [],
    }
    try:
        for directory in directories:
            run_id = directory.parent.name
            destination = batch / run_id / directory.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(f"Quarantine destination exists: {relative(destination)}")
            expected = {
                relative(file): file_hash(file)
                for file in walk_files(directory)
            }
            shutil.move(str(directory), str(destination))
            for original, digest in expected.items():
                relative_in_run = Path(original).relative_to(
                    Path("media/productions") / run_id / "render_work"
                )
                archived = batch / run_id / "render_work" / relative_in_run
                if not archived.is_file() or file_hash(archived) != digest:
                    raise IOError(f"Post-move hash verification failed for {original}")
                manifest["files"].append({
                    "original_path": original,
                    "quarantine_path": relative(archived),
                    "sha256": digest,
                    "size_bytes": archived.stat().st_size,
                })
        manifest_path = batch / "quarantine_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Quarantine manifest: {relative(manifest_path)}")
    except Exception:
        print(
            "Quarantine interrupted; do not delete the quarantine batch. "
            "Restore with the manifest once the issue is inspected.",
            file=sys.stderr,
        )
        raise


def command_restore(args: argparse.Namespace, policy: dict[str, Any]) -> None:
    manifest_input = ROOT / Path(args.manifest)
    ensure_no_symlink(manifest_input)
    manifest_path = manifest_input.resolve(strict=True)
    ensure_no_symlink(manifest_path)
    quarantine_root = (ROOT / Path(policy["quarantine_root"])).resolve()
    if quarantine_root not in manifest_path.parents or manifest_path.name != "quarantine_manifest.json":
        raise ValueError("Restore manifest must be inside the configured quarantine root.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("files", [])
    for item in items:
        source_input = ROOT / Path(item["quarantine_path"])
        destination_input = ROOT / Path(item["original_path"])
        ensure_no_symlink(source_input)
        ensure_no_symlink(destination_input)
        source = source_input.resolve(strict=True)
        destination = destination_input.resolve()
        if quarantine_root not in source.parents:
            raise ValueError("Restore source is outside the configured quarantine root.")
        if not source.is_file() or file_hash(source) != item["sha256"]:
            raise ValueError(f"Quarantined file is missing or changed: {item['quarantine_path']}")
        if destination.exists():
            raise FileExistsError(f"Restore will not overwrite: {item['original_path']}")
        if PRODUCTIONS_ROOT.resolve() not in destination.parents:
            raise ValueError("Restore target is outside media/productions.")
    if not args.confirm:
        print(json.dumps({"mode": "dry_run_restore", "file_count": len(items), "manifest": relative(manifest_path)}, indent=2))
        return
    for item in items:
        source = ROOT / Path(item["quarantine_path"])
        destination = ROOT / Path(item["original_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if file_hash(destination) != item["sha256"]:
            raise IOError(f"Restore hash verification failed: {item['original_path']}")
    manifest["restored_at"] = datetime.now().astimezone().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Restored {len(items)} files from {relative(manifest_path)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("plan", help="Show storage and candidate directories (dry-run only).")
    quarantine = subparsers.add_parser("quarantine", help="Move explicitly selected render_work directories.")
    quarantine.add_argument("--paths", nargs="+", required=True, help="Workspace-relative render_work directory paths.")
    quarantine.add_argument("--confirm", action="store_true", help="Execute the reversible quarantine.")
    quarantine.add_argument("--worker-idle-confirmed", action="store_true", help="Confirm an external process check found no worker.")
    quarantine.add_argument("--override-retention", action="store_true", help="Override the configured age threshold for reversible quarantine only.")
    restore = subparsers.add_parser("restore", help="Restore a quarantine manifest (dry-run unless confirmed).")
    restore.add_argument("--manifest", required=True, help="Workspace-relative quarantine_manifest.json path.")
    restore.add_argument("--confirm", action="store_true", help="Execute restoration without overwriting existing files.")
    args = parser.parse_args()
    policy = load_policy()
    try:
        if args.command in (None, "plan"):
            command_plan(args, policy)
        elif args.command == "quarantine":
            command_quarantine(args, policy)
        elif args.command == "restore":
            command_restore(args, policy)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"media-retention: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
