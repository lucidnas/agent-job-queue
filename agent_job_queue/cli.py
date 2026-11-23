"""agent-job-queue (aq): one machine-wide queue for heavy local jobs, built for AI coding agents.

Many agents (Claude Code, Codex, Gemini, scripts) share one machine. Each one thinks it is alone, so they all
start renders, encodes and local models at once and the machine falls over. aq makes heavy work take turns.

  aq status [--json] [--watch]      machine CPU/memory (and how much rq jobs use), running + waiting jobs
  aq run [-p PROJECT] [-n NAME] [-w WEIGHT] -- cmd args...
                                    wait for a turn (strict FIFO), run cmd, free the slot when it exits
  aq install [claude] [rules] [--block] [--uninstall]
                                    make agents aware of aq (session-start context + rule files); --block is opt-in
  aq capacity [N]                   show or set the total capacity (default 4)
  aq --version

Rules:
  - One project at a time: a job starts only when no job from another project is running
    (project = git top-level folder of the current directory unless -p is given).
  - Chunks: split big work into many short aq jobs (a few minutes each), so the queue can interleave.
  - Weights against capacity 4: 4 exclusive (renders, local TTS), 2 heavy (Whisper, long encodes), 1 light.

State lives in $AQ_HOME (default ~/.agent-job-queue), one JSON file per job. A job whose aq process died is dropped.
"""
import argparse, fcntl, json, os, platform, re, shlex, signal, subprocess, sys, time, uuid, shutil
from pathlib import Path

from . import __version__
HOME = Path(os.environ.get("AQ_HOME", Path.home() / ".agent-job-queue"))
JOBS = HOME / "jobs"; LOCK = HOME / "state.lock"; CONF = HOME / "config.json"
POLL = 3


def conf():
    try: return json.loads(CONF.read_text())
    except Exception: return {"capacity": 4}


class locked:
    def __enter__(self):
        HOME.mkdir(exist_ok=True); JOBS.mkdir(exist_ok=True)
        self.f = open(LOCK, "a+"); fcntl.flock(self.f, fcntl.LOCK_EX); return self
    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()


def alive(pid):
    try: os.kill(pid, 0); return True
    except ProcessLookupError: return False
    except PermissionError: return True


def jobs():
    out = []
    for p in JOBS.glob("*.json"):
        try: j = json.loads(p.read_text())
        except Exception: continue
        if not alive(j["pid"]): p.unlink(missing_ok=True); continue
        out.append(j)
    return sorted(out, key=lambda j: j["enqueued"])


def save(j): (JOBS / f"{j['id']}.json").write_text(json.dumps(j))


def fmt_age(t):
    s = int(time.time() - t); return f"{s//3600}h{s%3600//60:02d}m" if s >= 3600 else f"{s//60}m{s%60:02d}s"


# ---------- machine usage ----------

def mem_total_used():
    """(total_bytes, used_bytes) for macOS (vm_stat) or Linux (/proc/meminfo)."""
    try:
        if Path("/proc/meminfo").exists():
            m = {l.split(":")[0]: int(l.split()[1]) * 1024 for l in Path("/proc/meminfo").read_text().splitlines() if l.split()[1].isdigit()}
            return m["MemTotal"], m["MemTotal"] - m.get("MemAvailable", m.get("MemFree", 0))
        total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        page = int(re.search(r"page size of (\d+)", out).group(1))
        v = {l.split(":")[0].strip(): int(l.split(":")[1].strip(" .")) for l in out.splitlines()[1:] if ":" in l and l.split(":")[1].strip(" .").isdigit()}
        free = (v.get("Pages free", 0) + v.get("Pages inactive", 0) + v.get("Pages speculative", 0)) * page
        return total, total - free
    except Exception:
        return 0, 0


def processes():
    """{pid: (ppid, cpu_percent_of_one_core, rss_bytes)} for every process."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pcpu=,rss="], capture_output=True, text=True).stdout
    procs = {}
    for l in out.splitlines():
        f = l.split()
        if len(f) == 4:
            try: procs[int(f[0])] = (int(f[1]), float(f[2]), int(f[3]) * 1024)
            except ValueError: pass
    return procs


def tree_usage(root, procs, kids):
    """CPU% and RSS of a process and all its descendants."""
    cpu = rss = 0; stack = [root]; seen = set()
    while stack:
        p = stack.pop()
        if p in seen or p not in procs: continue
        seen.add(p); cpu += procs[p][1]; rss += procs[p][2]; stack += kids.get(p, [])
    return cpu, rss


def snapshot():
    with locked(): js = jobs()
    procs = processes(); kids = {}
    for pid, (pp, _, _) in procs.items(): kids.setdefault(pp, []).append(pid)
    ncpu = os.cpu_count() or 1
    total_cpu = sum(p[1] for p in procs.values())
    mem_total, mem_used = mem_total_used()
    rq_cpu = rq_mem = 0
    for j in js:
        if j["state"] == "running":
            c, m = tree_usage(j["pid"], procs, kids); j["cpu"] = round(c); j["mem"] = m; rq_cpu += c; rq_mem += m
    l1, l5, l15 = os.getloadavg()
    cap = conf()["capacity"]
    return {"version": __version__, "host": platform.node(), "cores": ncpu,
            "cpu": {"total_pct": min(100.0, total_cpu / ncpu), "rq_pct": min(100.0, rq_cpu / ncpu)},
            "mem": {"total": mem_total, "used": mem_used, "rq": rq_mem},
            "load": [round(l1, 2), round(l5, 2), round(l15, 2)],
            "capacity": {"total": cap, "used": sum(j["weight"] for j in js if j["state"] == "running")},
            "jobs": js}


# ---------- status view ----------

TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
def C(code, s): return f"\033[{code}m{s}\033[0m" if TTY else s
GB = 1024 ** 3


def bar(total_frac, rq_frac, width=28):
    """rq share in solid blocks, everything else in shaded blocks, free in dots."""
    rq_n = round(width * max(0, min(1, rq_frac)))
    tot_n = max(rq_n, round(width * max(0, min(1, total_frac))))
    rq_part = C("36", "█" * rq_n); other = C("33", "▓" * (tot_n - rq_n)); free = C("2", "·" * (width - tot_n))
    return f"[{rq_part}{other}{free}]"


def render(s):
    cpu, mem, cap = s["cpu"], s["mem"], s["capacity"]
    rq_c, oth_c = cpu["rq_pct"], cpu["total_pct"] - cpu["rq_pct"]
    rq_m, oth_m = mem["rq"] / GB, (mem["used"] - mem["rq"]) / GB
    head = C("1", "aq " + s["version"]) + "  %s  %d cores  %.0f GB" % (s["host"], s["cores"], mem["total"] / GB)
    cpu_l = "CPU  %s %5.0f%%   aq %s  other %s" % (bar(cpu["total_pct"] / 100, rq_c / 100), cpu["total_pct"], C("36", "%.0f%%" % rq_c), C("33", "%.0f%%" % oth_c))
    mem_l = "MEM  %s %5.1f/%.0f GB   aq %s  other %s" % (bar(mem["used"] / max(mem["total"], 1), mem["rq"] / max(mem["total"], 1)),
                                                     mem["used"] / GB, mem["total"] / GB, C("36", "%.1f GB" % rq_m), C("33", "%.1f GB" % oth_m))
    load_l = "LOAD %.1f %.1f %.1f   QUEUE %d/%d capacity   %s" % (*s["load"], cap["used"], cap["total"], C("2", "█ aq jobs  ▓ everything else  · free"))
    lines = [head, cpu_l, mem_l, load_l]
    run = [j for j in s["jobs"] if j["state"] == "running"]; wait = [j for j in s["jobs"] if j["state"] != "running"]
    if not s["jobs"]: lines.append(C("2", "queue empty"))
    if run:
        lines.append(C("1", "RUNNING"))
        for j in run:
            lines.append(f"  {C('32', '▶')} w{j['weight']} {fmt_age(j['started']):>7}  cpu {j.get('cpu', 0):4d}%  mem {j.get('mem', 0)/GB:4.1f}G  "
                         f"{C('35', '[' + j.get('project', '?') + ']')} {j['name']}")
    if wait:
        lines.append(C("1", "WAITING"))
        for n, j in enumerate(wait, 1):
            lines.append(f"  {n:>2} w{j['weight']} waited {fmt_age(j['enqueued']):>7}  {C('35', '[' + j.get('project', '?') + ']')} {j['name']}")
    return "\n".join(lines)


def status(a):
    if a.json:
        print(json.dumps(snapshot(), default=str)); return
    if not a.watch:
        print(render(snapshot())); return
    try:
        while True:
            s = render(snapshot()); sys.stdout.write("\033[H\033[2J" + s + "\n" + C("2", "(Ctrl-C to stop)") + "\n"); sys.stdout.flush(); time.sleep(2)
    except KeyboardInterrupt:
        pass


def capacity(a):
    if a.n is None: print(conf()["capacity"]); return
    with locked(): c = conf(); c["capacity"] = a.n; CONF.write_text(json.dumps(c))
    print("capacity", a.n)


# ---------- run ----------

def project_of(cwd):
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True)
    return Path(r.stdout.strip() if r.returncode == 0 else cwd).name


def run(a):
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd: sys.exit("aq run: give a command after --")
    j = {"id": uuid.uuid4().hex[:10], "pid": os.getpid(), "name": a.name or " ".join(cmd)[:100], "weight": a.weight,
         "project": a.project or project_of(os.getcwd()), "cmd": cmd, "cwd": os.getcwd(), "state": "waiting", "enqueued": time.time()}
    with locked(): save(j)
    told = False
    try:
        while True:
            with locked():
                js = jobs(); cap = max(conf()["capacity"], a.weight)
                used = sum(x["weight"] for x in js if x["state"] == "running")
                waiting = [x for x in js if x["state"] == "waiting"]
                other = {x.get("project") for x in js if x["state"] == "running"} - {j["project"]}
                if waiting and waiting[0]["id"] == j["id"] and used + a.weight <= cap and not other:
                    j["state"] = "running"; j["started"] = time.time(); save(j); break
            if not told:
                ahead = [x["name"] for x in js if x["id"] != j["id"] and (x["state"] == "running" or x["enqueued"] < j["enqueued"])]
                print(f"[aq] waiting for {len(ahead)} job(s): " + "; ".join(ahead[:4]), file=sys.stderr, flush=True); told = True
            time.sleep(POLL)
        print(f"[aq] start {j['name']} (w{a.weight})", file=sys.stderr, flush=True)
        p = subprocess.Popen(cmd)
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(s, lambda sig, f: p.send_signal(sig))
        rc = p.wait()
    finally:
        with locked(): (JOBS / f"{j['id']}.json").unlink(missing_ok=True)
    print(f"[aq] done {j['name']} rc={rc} in {fmt_age(j['started'])}", file=sys.stderr, flush=True)
    sys.exit(rc)


# ---------- install into agents ----------

RULE_START, RULE_END = "<!-- agent-job-queue:start -->", "<!-- agent-job-queue:end -->"
RULE = RULE_START + """
## Machine queue: `aq` (agent-job-queue, all agents)
- Heavy local work on this machine (renders, video encodes, local Whisper/TTS models, Blender, big builds) runs ONLY through `aq run -p <project> -n "<name>" -w <4 exclusive | 2 heavy | 1 light> -- <command>`.
- Run `aq status` first. One project runs at a time. Split long jobs into chunks of a few minutes, each its own `aq run`.
- Long waits: run it in the background and wait for it to exit; don't poll.
""" + RULE_END
HERE = Path(__file__).resolve().parent  # package dir; hooks/ ships inside it
HOOK_FILES = ("hooks/session_start.sh", "hooks/pre_bash.py")


def _merge_rule(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else ""
    if RULE_START not in old and re.search(r"machine queue: `(aq|rq)`", old, re.I):
        return "already has a queue section (left alone)"
    if RULE_START in old:
        new = old[:old.index(RULE_START)] + RULE + old[old.index(RULE_END) + len(RULE_END):]
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + RULE + "\n"
    if new != old: path.write_text(new); return "updated"
    return "unchanged"


def _claude_hooks(uninstall=False, block=False):
    f = Path.home() / ".claude/settings.json"
    d = json.loads(f.read_text()) if f.exists() else {}
    hooks = d.setdefault("hooks", {})
    # run hooks through an interpreter so they work even if the package install dropped the executable bit
    want = {"SessionStart": {"hooks": [{"type": "command", "command": "sh " + shlex.quote(str(HERE / HOOK_FILES[0])), "timeout": 10}]},
            "PreToolUse": {"matcher": "Bash", "hooks": [{"type": "command", "command": shlex.quote(sys.executable) + " " + shlex.quote(str(HERE / HOOK_FILES[1])), "timeout": 10}]}}
    if not block: want.pop("PreToolUse")   # blocking is opt-in (--block); session-start context is the default
    for ev in ("SessionStart", "PreToolUse"):
        entry = want.get(ev)
        lst = [e for e in hooks.get(ev, []) if not any(h.get("command", "").strip().strip("'").endswith(HOOK_FILES) for h in e.get("hooks", []))]
        if not uninstall and entry: lst.append(entry)
        if lst: hooks[ev] = lst
        else: hooks.pop(ev, None)
    if not hooks: d.pop("hooks", None)
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists(): (f.parent / "settings.json.agent-job-queue-backup").write_text(f.read_text())
    f.write_text(json.dumps(d, indent=2) + "\n")
    return f


def install(a):
    targets = a.targets or ["claude", "rules"]
    home = Path.home()
    if "claude" in targets:
        f = _claude_hooks(a.uninstall, a.block)
        print("claude hooks", "removed from" if a.uninstall else ("session-start + blocking added to" if a.block else "session-start added to (blocking off; add --block to enable)"), f)
    if "rules" in targets and not a.uninstall:
        for f in [home / "AGENTS.md", home / ".claude/CLAUDE.md", home / ".codex/AGENTS.md", home / ".gemini/GEMINI.md"]:
            agent = {".codex": "codex", ".gemini": "gemini"}.get(f.parent.name)
            if (agent and shutil.which(agent)) or (not agent and f.parent.exists() and (f.exists() or f.parent == home / ".claude")):
                print("rule", _merge_rule(f), f)


ap = argparse.ArgumentParser(prog="aq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--version", action="version", version=f"agent-job-queue {__version__}")
sp = ap.add_subparsers(dest="c", required=True)
st = sp.add_parser("status", help="machine usage + queue"); st.add_argument("--json", action="store_true"); st.add_argument("--watch", action="store_true"); st.set_defaults(f=status)
r = sp.add_parser("run", help="queue a command"); r.add_argument("-p", "--project"); r.add_argument("-n", "--name"); r.add_argument("-w", "--weight", type=int, default=4); r.add_argument("cmd", nargs=argparse.REMAINDER); r.set_defaults(f=run)
i = sp.add_parser("install", help="make coding agents aware of aq"); i.add_argument("targets", nargs="*", choices=["claude", "rules"]); i.add_argument("--uninstall", action="store_true"); i.add_argument("--block", action="store_true", help="also block heavy commands not run through aq (opt-in)"); i.set_defaults(f=install)
c = sp.add_parser("capacity", help="show or set capacity"); c.add_argument("n", nargs="?", type=int); c.set_defaults(f=capacity)

def main():
    a = ap.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
