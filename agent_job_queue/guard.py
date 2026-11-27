"""aq guard: is every registered project in git and pushed, are assets on the SSD, is there disk space left?

Warns only. It never commits, pushes or deletes. `--fix` only runs `git init`, writes a `.gitignore`
and creates a private GitHub repo with `gh` (when `gh` is installed and signed in).
"""
import os, shutil, subprocess
from pathlib import Path

from .state import conf

MEDIA_EXT = [".mp4", ".mov", ".wav", ".mp3", ".m4a", ".png", ".jpg", ".jpeg", ".webm", ".mkv", ".aiff", ".flac"]
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next"}
GITIGNORE = """.env
.env.*
!.env.example
node_modules/
__pycache__/
.venv/
dist/
build/
.DS_Store
*.mp4
*.mov
*.wav
*.mp3
*.m4a
"""
GB = 1024 ** 3


def _git(d, *a):
    try: return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(a, 1, "", str(e))


def _on_ssd(path, c):
    ssd = c.get("ssd_path")
    if not ssd: return False
    r, s = os.path.realpath(path), os.path.realpath(ssd).rstrip("/")
    return r == s or r.startswith(s + "/")


def _media(path, c):
    limit = c.get("media_mb", 20) * 1024 * 1024; ext = tuple(c.get("media_ext", MEDIA_EXT)); hits = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.lower().endswith(ext):
                fp = os.path.join(root, f)
                try:
                    if os.path.getsize(fp) > limit: hits.append(fp)
                except OSError: pass
    return hits


def check_project(p, media=True):
    c = conf(); out = []; tag = f"[{p['name']}]"
    for path in p.get("paths", []):
        if not os.path.isdir(path):
            out.append(f"{tag} path missing: {path} (SSD unplugged?)"); continue
        top = _git(path, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            out.append(f"{tag} no git repo at {path} (aq guard -p {p['name']} --fix)")
        else:
            st = _git(path, "status", "--porcelain")
            n = len([l for l in st.stdout.splitlines() if l.strip()])
            if n: out.append(f"{tag} {n} uncommitted change(s) in {path}")
            if _git(path, "rev-parse", "--verify", "-q", "HEAD").returncode == 0:
                up = _git(path, "rev-list", "--count", "@{u}..HEAD")
                if up.returncode != 0:
                    out.append(f"{tag} branch has no upstream (push it: git push -u origin HEAD)")
                elif up.stdout.strip() not in ("", "0"):
                    out.append(f"{tag} {up.stdout.strip()} unpushed commit(s)")
        if media and not _on_ssd(path, c):
            hits = _media(path, c)
            if hits:
                out.append(f"{tag} {len(hits)} media file(s) over {c.get('media_mb', 20)} MB on the Mac; move to SSD: " + ", ".join(hits[:3]) + (" ..." if len(hits) > 3 else ""))
    return out


def _free_gb(path):
    path = Path(path)
    while not path.exists() and path != path.parent: path = path.parent
    st = os.statvfs(str(path)); return st.f_bavail * st.f_frsize / GB


def check_disks():
    c = conf(); out = []
    mac = _free_gb(str(Path.home()))
    if mac < c.get("mac_min_gb", 15): out.append(f"[disk] Mac has {mac:.1f} GB free (warn below {c.get('mac_min_gb', 15)} GB)")
    ssd = c.get("ssd_path")
    if ssd:
        if not os.path.isdir(ssd): out.append(f"[disk] SSD not mounted at {ssd}")
        else:
            f = _free_gb(ssd)
            if f < c.get("ssd_min_gb", 50): out.append(f"[disk] SSD has {f:.1f} GB free (warn below {c.get('ssd_min_gb', 50)} GB)")
    return out


def check_all(media=True, only=None):
    from .projects import all_projects
    out = check_disks()
    for p in all_projects():
        if p["name"] == "inbox" or p.get("state") == "done" or (only and p["name"] != only): continue
        out += check_project(p, media)
    return out


def fix_project(p):
    done = []
    for path in p.get("paths", []):
        if not os.path.isdir(path) or _git(path, "rev-parse", "--show-toplevel").returncode == 0: continue
        _git(path, "init", "-q"); done.append(f"git init {path}")
        gi = Path(path) / ".gitignore"
        if not gi.exists(): gi.write_text(GITIGNORE); done.append(f"wrote {gi}")
        if not shutil.which("gh"):
            done.append("gh not installed; repo created locally only"); continue
        r = subprocess.run(["gh", "repo", "create", p["name"], "--private", "--source", path, "--remote", "origin"],
                           capture_output=True, text=True)
        done.append(f"gh repo create {p['name']} --private: " + ("ok" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["failed"])[-1]))
    return done
