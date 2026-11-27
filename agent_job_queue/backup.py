"""aq backup: keep work off the Mac's disk and off a single drive. Runs hourly from a LaunchAgent.

1. If the SSD (config `ssd_path`) isn't mounted: record it, notify, stop.
2. Copy whole Claude sessions older than 7 days from ~/.claude/projects to <ssd>/claude-history/projects
   (memory/ folders are never touched).
3. `rclone copy` the history, then the backup paths, to `backup.remote` (adds and updates only; files it would
   overwrite are kept in a dated -versions folder). Nothing is ever deleted on the remote.
4. Only when `rclone check` confirms the history is on the remote too are the Mac copies removed, so a session is
   always on at least two disks. With no remote set, sessions stay on the Mac (unless history.allow_single_copy).
5. Write $AQ_HOME/backup.json (shown on `aq board`) and a line in backup.log.
"""
import fcntl, json, os, plistlib, shutil, subprocess, sys, time
from pathlib import Path

from .state import HOME, conf

LABEL = "com.agent-job-queue.backup"
PKG_PARENT = str(Path(__file__).resolve().parents[1])
EXCLUDE = ["tmp/**", "work/**", "**/cache/**", "**/.cache/**", "**/node_modules/**", "._*", ".DS_Store",
           ".Spotlight-V100/**", ".Trashes/**", ".fseventsd/**", ".TemporaryItems/**"]
BUDGET_MIN = 50


class SSDMissing(Exception):
    pass


def _skip(path): return "memory" in Path(path).parts


def ssd_ok(path):
    """The SSD counts as present only if it is a real mount point (a leftover folder under /Volumes is not)."""
    if not path: return False
    return os.path.ismount(path) if conf().get("ssd_must_be_mount", True) else os.path.isdir(path)


def _copy_file(a, b):
    """Copy a -> b (unless an equal-size copy is there), flush it to disk and check the size. True if b matches."""
    try:
        size = a.stat().st_size
        if not (b.exists() and b.stat().st_size == size):
            b.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(a, b)
            with open(b, "rb") as fh: os.fsync(fh.fileno())
        return os.stat(b).st_size == size
    except OSError:
        return False


def sessions(src, days=7, now=None):
    """Old sessions: (jsonl, folder-or-None), judged by the .jsonl's modified time."""
    cutoff = (now or time.time()) - days * 86400
    for j in sorted(Path(src).glob("*/*.jsonl")):
        if _skip(j.relative_to(src)): continue
        try:
            if j.stat().st_mtime >= cutoff: continue
        except OSError: continue
        d = j.with_suffix("")
        yield j, (d if d.is_dir() else None)


def move_history(src, dst, days=7, now=None, root=None, dry_run=False, remove=True):
    """Copy old sessions to dst; with remove=True also delete the Mac copy of every file whose dst copy matches.
    A session's .jsonl is handled last and is kept if any of its files failed, so a half-moved session stays resumable."""
    src, dst = Path(src), Path(dst); root = Path(root) if root else dst.parent.parent   # <ssd>/claude-history/projects
    if not root.is_dir(): raise SSDMissing(str(root))
    r = {"moved": 0, "files": 0, "skipped": 0, "bytes": 0, "removed_from_mac": remove and not dry_run}
    if not src.is_dir(): return r
    for j, d in sessions(src, days, now):
        files = [j] + ([f for f in d.rglob("*") if f.is_file() and not _skip(f.relative_to(src))] if d else [])
        if dry_run:
            r["moved"] += 1; r["files"] += len(files); r["bytes"] += sum(f.stat().st_size for f in files); continue
        ok = True
        for f in files[1:] + files[:1]:
            if f == j and not ok: break
            size = f.stat().st_size
            if _copy_file(f, dst / f.relative_to(src)):
                r["files"] += 1; r["bytes"] += size
                if remove:
                    try: f.unlink()
                    except OSError: ok = False
            else: ok = False; r["skipped"] += 1
        if ok: r["moved"] += 1
        if d and remove:
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) + [d]:
                try: sub.rmdir()
                except OSError: pass
    return r


def restore(session_id, src, dst, force=False):
    """Copy a session back to the Mac and mark it as just used, so the next hourly run doesn't move it straight back.
    Refuses to overwrite a copy already on the Mac (it may be newer: you resumed it) unless force."""
    src, dst = Path(src), Path(dst); hits = list(dst.glob(f"*/{session_id}.jsonl"))
    if not hits: raise FileNotFoundError(f"no session {session_id} under {dst}")
    j = hits[0]; rel = j.relative_to(dst); target = src / rel
    if target.exists() and not force: raise FileExistsError(f"{target} is already on the Mac (use --force to overwrite it)")
    target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(j, target)
    d = j.with_suffix("")
    if d.is_dir(): shutil.copytree(d, target.with_suffix(""), dirs_exist_ok=True)
    os.utime(target, None)
    return target


def versions_dir(remote, day=None):
    """Where rclone keeps files it would overwrite: a sibling of the destination, one folder per day."""
    name, _, path = remote.partition(":")
    return f"{name}:{(path.rstrip('/') or 'aq') + '-versions'}/{day or time.strftime('%Y-%m-%d')}"


def drive_copy(paths, remote, excludes, runner=subprocess.run, budget_min=BUDGET_MIN, extra=()):
    if not shutil.which("rclone"): return [{"path": p, "error": "rclone not installed"} for p in paths]
    jobs = []
    for p in paths:
        p = Path(p); name = p.name or "root"
        if (p / "claude-history").is_dir(): jobs.append((p / "claude-history", f"{remote}/{name}/claude-history"))
        jobs.append((p, f"{remote}/{name}"))
    out = []; deadline = time.time() + budget_min * 60
    for path, dest in jobs:
        left = int((deadline - time.time()) // 60)
        if left < 1: out.append({"path": str(path), "skipped": "out of time this hour"}); continue
        argv = ["rclone", "copy", str(path), dest, "--backup-dir", versions_dir(remote) + "/" + dest.split(":", 1)[-1].replace("/", "_"),
                "--max-duration", f"{left}m", "--cutoff-mode", "soft",
                "--transfers", "4", "--stats-one-line", "--stats", "0"]
        for e in excludes: argv += ["--exclude", e]
        argv += list(extra)
        r = runner(argv, capture_output=True, text=True)
        # rclone exits 0 when done; with --max-duration it may stop early and resume next hour
        out.append({"path": str(path), "dest": dest, "rc": r.returncode, "tail": (r.stderr or "").strip()[-300:]})
    return out


def _notify(msg):
    if shutil.which("osascript"):
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "aq backup"'], capture_output=True)


def run_backup(runner=subprocess.run, notify=True, dry_run=False):
    c = conf(); b = c.get("backup", {}); h = c.get("history", {}); ssd = c.get("ssd_path")
    state_f = HOME / "backup.json"
    try: prev = json.loads(state_f.read_text())
    except Exception: prev = {}
    r = {"last_run": time.time(), "last_success": prev.get("last_success"), "ssd_missing": False, "history": None, "drive": []}
    if not ssd_ok(ssd):
        r["ssd_missing"] = True; r["error"] = "SSD not mounted" if ssd else "no ssd_path set (aq backup --ssd PATH)"
        if notify and ssd: _notify(f"SSD MISSING at {ssd}: nothing backed up")
    else:
        src = Path(h.get("src", Path.home() / ".claude/projects")).expanduser()
        dst = Path(h.get("dst", Path(ssd) / "claude-history/projects")).expanduser()
        remote = b.get("remote"); hist_dir = Path(ssd) / "claude-history"
        try:
            # 1. copy old sessions to the SSD, keeping the Mac copy for now
            r["history"] = move_history(src, dst, h.get("days", 7), root=ssd, dry_run=dry_run, remove=False)
            if remote and not dry_run:
                extra = ["--bwlimit", b["bwlimit"]] if b.get("bwlimit") else []
                excludes = b.get("exclude", EXCLUDE)
                r["drive"] = drive_copy(b.get("paths", [ssd]), remote, excludes, runner=runner, extra=extra)
                # 2. remove from the Mac only what the remote is confirmed to hold
                rdest = f"{remote}/{Path(ssd).name}/claude-history"
                on_remote = False
                if str(dst).startswith(str(hist_dir) + "/"):
                    chk = runner(["rclone", "check", str(hist_dir), rdest, "--one-way", "--size-only", *sum((["--exclude", e] for e in excludes), [])],
                                 capture_output=True, text=True)
                    on_remote = chk.returncode == 0
                r["history"]["on_remote"] = on_remote
                if on_remote:
                    r["history"]["removed"] = move_history(src, dst, h.get("days", 7), root=ssd)
                else:
                    r["history"]["note"] = "kept on the Mac until the cloud copy is complete"
            elif not remote:
                r["drive"] = [{"skipped": "no backup.remote set"}]
                if h.get("allow_single_copy") and not dry_run:
                    r["history"]["removed"] = move_history(src, dst, h.get("days", 7), root=ssd)
                else:
                    r["history"]["note"] = "kept on the Mac: set backup.remote so it lives on two disks"
        except SSDMissing:
            r["ssd_missing"] = True
        bad = [d for d in r["drive"] if d.get("error") or d.get("rc") not in (None, 0) or d.get("skipped") == "out of time this hour"]
        if not r["ssd_missing"] and not bad: r["last_success"] = r["last_run"]
    if not dry_run:
        HOME.mkdir(parents=True, exist_ok=True); state_f.write_text(json.dumps(r, indent=2))
        with open(HOME / "backup.log", "a") as log: log.write(json.dumps(r) + "\n")
    return r


def run_locked(**kw):
    HOME.mkdir(parents=True, exist_ok=True); f = open(HOME / "backup.lock", "a+")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: return None
    try: return run_backup(**kw)
    finally: fcntl.flock(f, fcntl.LOCK_UN); f.close()


def plist_text(python, aq_home):
    return plistlib.dumps({
        "Label": LABEL, "ProgramArguments": [python, "-m", "agent_job_queue", "backup"],
        "StartInterval": 3600, "RunAtLoad": True, "ProcessType": "Background", "LowPriorityIO": True,
        "WorkingDirectory": PKG_PARENT,
        "EnvironmentVariables": {"AQ_HOME": aq_home, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                                 "PYTHONPATH": PKG_PARENT},   # works for a git checkout as well as a pip install
        "StandardOutPath": str(Path(aq_home) / "backup.out"), "StandardErrorPath": str(Path(aq_home) / "backup.out"),
    }).decode()


def install_backup(uninstall=False):
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"; dom = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True)
    if uninstall:
        plist.unlink(missing_ok=True); return f"removed {plist}"
    plist.parent.mkdir(parents=True, exist_ok=True); HOME.mkdir(parents=True, exist_ok=True)
    plist.write_text(plist_text(sys.executable, str(HOME)))
    r = subprocess.run(["launchctl", "bootstrap", dom, str(plist)], capture_output=True, text=True)
    msg = f"hourly backup installed: {plist}" if r.returncode == 0 else f"wrote {plist} but launchctl said: {r.stderr.strip()}"
    return (msg + f"\nIf macOS blocks it from reading ~/.claude or the SSD, give Full Disk Access to {os.path.realpath(sys.executable)}"
            " (System Settings > Privacy & Security > Full Disk Access).")
