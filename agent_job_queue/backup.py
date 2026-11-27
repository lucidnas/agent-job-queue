"""aq backup: keep work off the Mac's disk and off a single drive. Runs hourly from a LaunchAgent.

1. If the SSD (config `ssd_path`) isn't mounted: record it, notify, stop.
2. Move whole Claude sessions older than 7 days from ~/.claude/projects to <ssd>/claude-history/projects
   (copy, check size, then remove; memory/ folders are never touched).
3. `rclone copy` the backup paths to `backup.remote` (adds and updates only, never deletes on the remote).
4. Write $AQ_HOME/backup.json (shown on `aq board`) and a line in backup.log.
"""
import fcntl, json, os, plistlib, shutil, subprocess, sys, time
from pathlib import Path

from .state import HOME, conf

LABEL = "com.agent-job-queue.backup"
EXCLUDE = ["tmp/**", "work/**", "**/cache/**", "**/.cache/**", "**/node_modules/**", "._*", ".DS_Store",
           ".Spotlight-V100/**", ".Trashes/**", ".fseventsd/**", ".TemporaryItems/**"]
BUDGET_MIN = 50


class SSDMissing(Exception):
    pass


def _skip(path): return "memory" in Path(path).parts


def _move_file(a, b):
    """Copy a -> b (unless an equal-size copy is there), check the size, then remove a. True if a was removed."""
    try:
        size = a.stat().st_size
        if not (b.exists() and b.stat().st_size == size):
            b.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(a, b)
        if b.stat().st_size != size: return False
        a.unlink(); return True
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


def move_history(src, dst, days=7, now=None, root=None, dry_run=False):
    src, dst = Path(src), Path(dst); root = Path(root) if root else dst.parent.parent   # <ssd>/claude-history/projects
    if not root.is_dir(): raise SSDMissing(str(root))
    r = {"moved": 0, "files": 0, "skipped": 0, "bytes": 0}
    if not src.is_dir(): return r
    for j, d in sessions(src, days, now):
        files = [j] + ([f for f in d.rglob("*") if f.is_file() and not _skip(f.relative_to(src))] if d else [])
        if dry_run:
            r["moved"] += 1; r["files"] += len(files); r["bytes"] += sum(f.stat().st_size for f in files); continue
        ok = True
        for f in files[1:] + files[:1]:                # folder first, the .jsonl last: if anything fails the .jsonl stays
            if f == j and not ok: break
            size = f.stat().st_size
            if _move_file(f, dst / f.relative_to(src)): r["files"] += 1; r["bytes"] += size
            else: ok = False; r["skipped"] += 1
        if ok: r["moved"] += 1
        if d:
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)) + [d]:
                try: sub.rmdir()
                except OSError: pass
    return r


def restore(session_id, src, dst):
    src, dst = Path(src), Path(dst); hits = list(dst.glob(f"*/{session_id}.jsonl"))
    if not hits: raise FileNotFoundError(f"no session {session_id} under {dst}")
    j = hits[0]; rel = j.relative_to(dst)
    (src / rel).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(j, src / rel)
    d = j.with_suffix("")
    if d.is_dir(): shutil.copytree(d, src / rel.with_suffix(""), dirs_exist_ok=True)
    return src / rel


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
        argv = ["rclone", "copy", str(path), dest, "--max-duration", f"{left}m", "--cutoff-mode", "soft",
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
    c = conf(); b = c.get("backup", {}); ssd = c.get("ssd_path")
    state_f = HOME / "backup.json"
    try: prev = json.loads(state_f.read_text())
    except Exception: prev = {}
    r = {"last_run": time.time(), "last_success": prev.get("last_success"), "ssd_missing": False, "history": None, "drive": []}
    if not ssd or not os.path.isdir(ssd):
        r["ssd_missing"] = True; r["error"] = "SSD not mounted" if ssd else "no ssd_path set (aq backup --ssd PATH)"
        if notify and ssd: _notify(f"SSD MISSING at {ssd}: nothing backed up")
    else:
        h = c.get("history", {})
        src = Path(h.get("src", Path.home() / ".claude/projects")).expanduser()
        dst = Path(h.get("dst", Path(ssd) / "claude-history/projects")).expanduser()
        try: r["history"] = move_history(src, dst, h.get("days", 7), root=ssd, dry_run=dry_run)
        except SSDMissing: r["ssd_missing"] = True
        if b.get("remote") and not dry_run:
            extra = ["--bwlimit", b["bwlimit"]] if b.get("bwlimit") else []
            r["drive"] = drive_copy(b.get("paths", [ssd]), b["remote"], b.get("exclude", EXCLUDE), runner=runner, extra=extra)
        elif not b.get("remote"):
            r["drive"] = [{"skipped": "no backup.remote set"}]
        bad = [d for d in r["drive"] if d.get("error") or d.get("rc") not in (None, 0)]
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
        "EnvironmentVariables": {"AQ_HOME": aq_home, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
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
