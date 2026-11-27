"""Projects, the one focus project, idea inboxes and the board.

  ~/.agent-job-queue/projects/<name>.json   one file per project
  ~/.agent-job-queue/focus.json             {project, waiting_on, since, set_by}
  ~/.agent-job-queue/focus.log              one JSON line per focus switch
"""
import json, os, re, sys, time
from pathlib import Path

from .state import HOME, locked

PROJECTS = HOME / "projects"; FOCUS = HOME / "focus.json"; FOCUS_LOG = HOME / "focus.log"; BACKUP_STATE = HOME / "backup.json"
STATES = ("active", "queued", "parked", "done")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class NoProject(Exception):
    pass


def who(by=None):
    return by or os.environ.get("CLAUDE_SESSION_ID") or "owner"


def _file(name): return PROJECTS / f"{name}.json"


def load(name):
    try: return json.loads(_file(name).read_text())
    except Exception: return None


def need(name):
    p = load(name)
    if p is None: raise NoProject(name)
    return p


def save_project(p):
    PROJECTS.mkdir(parents=True, exist_ok=True)
    tmp = _file(p["name"]).with_suffix(".tmp"); tmp.write_text(json.dumps(p, indent=2)); tmp.replace(_file(p["name"]))


def all_projects():
    out = []
    for f in sorted(PROJECTS.glob("*.json")) if PROJECTS.exists() else []:
        try: out.append(json.loads(f.read_text()))
        except Exception: pass
    return out


def _new(name, paths=(), goal="", priority=0, remote=None):
    return {"name": name, "paths": list(paths), "remote": remote, "goal": goal, "priority": priority,
            "state": "queued", "inbox": [], "last_touched": time.time()}


def add(name, paths, goal="", priority=0, remote=None):
    if not NAME_RE.match(name): raise ValueError(f'bad project name "{name}" (letters, digits, . _ -)')
    with locked():
        p = load(name) or _new(name)
        for x in paths:
            if x not in p["paths"]: p["paths"].append(x)
        if goal: p["goal"] = goal
        if priority: p["priority"] = priority
        if remote: p["remote"] = remote
        save_project(p)
    return p


def _real(path):
    return os.path.realpath(os.path.expanduser(str(path))).rstrip("/") or "/"


def match_path(path):
    """Name of the registered project whose path contains `path` (longest wins), or None."""
    target = _real(path); best = (None, -1)
    for p in all_projects():
        for root in p.get("paths", []):
            r = _real(root)
            if (target == r or target.startswith(r.rstrip("/") + "/")) and len(r) > best[1]:
                best = (p["name"], len(r))
    return best[0]


# ---------- focus ----------

def get_focus():
    try: f = json.loads(FOCUS.read_text())
    except Exception: f = {}
    return {"project": f.get("project"), "waiting_on": f.get("waiting_on"), "since": f.get("since"), "set_by": f.get("set_by")}


def _write_focus(f): HOME.mkdir(parents=True, exist_ok=True); FOCUS.write_text(json.dumps(f))


def set_focus(name, by=None):
    with locked():
        new = need(name); old = get_focus()
        if old["project"] and old["project"] != name:
            o = load(old["project"])
            if o and o["state"] == "active": o["state"] = "queued"; save_project(o)
        new["state"] = "active"; new["last_touched"] = time.time(); save_project(new)
        f = {"project": name, "waiting_on": None, "since": time.time(), "set_by": who(by)}; _write_focus(f)
        with open(FOCUS_LOG, "a") as log:
            log.write(json.dumps({"t": f["since"], "from": old["project"], "to": name, "by": f["set_by"]}) + "\n")
    return f


def wait(what):
    with locked():
        f = get_focus()
        if not f["project"]: raise ValueError("no focus set; run aq focus <project> first")
        f["waiting_on"] = what; f["since"] = time.time(); _write_focus(f)
    return f


def resume():
    with locked():
        f = get_focus(); f["waiting_on"] = None; f["since"] = time.time(); _write_focus(f)
    return f


def set_state(name, state):
    if state not in STATES: raise ValueError(state)
    with locked():
        p = need(name); p["state"] = state; save_project(p)
    return p


# ---------- ideas ----------

def add_idea(text, project=None, by=None):
    name = project or "inbox"
    with locked():
        p = load(name)
        if p is None:
            if project: raise NoProject(project)
            p = _new("inbox", goal="unsorted ideas; the main chat files them under projects")
        item = {"t": time.time(), "text": text, "by": who(by)}
        p["inbox"].append(item); p["last_touched"] = item["t"]; save_project(p)
    return item


def next_up():
    f = get_focus()
    if f["project"] and not f["waiting_on"]:
        return {"project": f["project"], "why": "the focus project"}
    cands = [p for p in all_projects() if p["name"] != f["project"] and p["state"] == "queued" and p["inbox"]]
    cands.sort(key=lambda p: (-p.get("priority", 0), p["inbox"][0]["t"]))
    if cands:
        return {"project": cands[0]["name"], "why": f"focus is waiting on {f['waiting_on']}; top queued project with ideas"}
    return {"project": f["project"], "why": "nothing else queued" if f["project"] else "no focus set (aq focus <project>)"}


# ---------- board ----------

def _age(t):
    s = int(time.time() - (t or time.time()))
    return f"{s//86400}d" if s >= 86400 else (f"{s//3600}h{s%3600//60:02d}m" if s >= 3600 else f"{s//60}m")


def board_data(warnings=True, quick=False):
    f = get_focus()
    last = None
    try: last = json.loads(FOCUS_LOG.read_text().splitlines()[-1])
    except Exception: pass
    order = {s: i for i, s in enumerate(STATES)}
    ps = sorted(all_projects(), key=lambda p: (p["name"] != f["project"], order.get(p["state"], 9), -p.get("priority", 0), p["name"]))
    try: b = json.loads(BACKUP_STATE.read_text())
    except Exception: b = {}
    w = []
    if warnings:
        try:
            from . import guard
            w = guard.check_disks() if quick else guard.check_all(media=False)
        except ImportError:
            pass
    if b.get("ssd_missing"): w.insert(0, "[backup] SSD MISSING at last backup run")
    return {"focus": f, "last_switch": last,
            "projects": [{"name": p["name"], "state": p["state"], "priority": p.get("priority", 0), "goal": p.get("goal", ""),
                          "inbox_count": len(p["inbox"]), "last_touched": p.get("last_touched")} for p in ps],
            "warnings": w, "backup": {"last_success": b.get("last_success"), "ssd_missing": bool(b.get("ssd_missing"))}}


def render_board(d):
    f = d["focus"]
    if not f["project"]: head = "FOCUS none (aq focus <project>)"
    elif f["waiting_on"]: head = f"FOCUS {f['project']} (waiting on {f['waiting_on']} for {_age(f['since'])})"
    else: head = f"FOCUS {f['project']}"
    lines = [head]
    if d["last_switch"]:
        s = d["last_switch"]; lines.append(f"  last switch {_age(s['t'])} ago: {s['from'] or '-'} -> {s['to']} by {s['by']}")
    if d["projects"]:
        lines.append("PROJECTS")
        for p in d["projects"]:
            mark = "*" if p["name"] == f["project"] else " "
            lines.append(f" {mark} {p['name']:<18} {p['state']:<7} p{p['priority']:<2} ideas {p['inbox_count']:<3} {p['goal'][:50]}")
    b = d["backup"]
    lines.append("BACKUP " + (f"last success {_age(b['last_success'])} ago" if b["last_success"] else "never run (aq install backup)"))
    if d["warnings"]:
        lines.append("WARNINGS"); lines += ["  " + x for x in d["warnings"][:25]]
        if len(d["warnings"]) > 25: lines.append(f"  ... {len(d['warnings']) - 25} more (aq guard)")
    return "\n".join(lines)


def render_inbox(p):
    return "\n".join(f"  {i+1:>2}. {x['text']}  ({_age(x['t'])} ago, {x['by']})" for i, x in enumerate(p["inbox"])) or "  (empty)"
