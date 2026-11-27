"""Shared state: the aq home folder, its config and the one file lock every writer takes."""
import fcntl, json, os
from pathlib import Path

HOME = Path(os.environ.get("AQ_HOME", Path.home() / ".agent-job-queue"))
JOBS = HOME / "jobs"; LOCK = HOME / "state.lock"; CONF = HOME / "config.json"


def conf():
    try: c = json.loads(CONF.read_text())
    except Exception: c = {}
    c.setdefault("capacity", 4); return c


def write_conf(c): HOME.mkdir(parents=True, exist_ok=True); CONF.write_text(json.dumps(c, indent=2))


class locked:
    def __enter__(self):
        HOME.mkdir(parents=True, exist_ok=True); JOBS.mkdir(exist_ok=True)
        self.f = open(LOCK, "a+"); fcntl.flock(self.f, fcntl.LOCK_EX); return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()
