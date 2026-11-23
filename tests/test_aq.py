import json, os, subprocess, sys, time
from pathlib import Path

AQ = [sys.executable, "-m", "agent_job_queue"]
ROOT = Path(__file__).resolve().parents[1]


def env(tmp):
    e = dict(os.environ, AQ_HOME=str(tmp / "state"), HOME=str(tmp / "home"), PYTHONPATH=str(ROOT))
    (tmp / "home").mkdir(exist_ok=True); return e


def start(tmp, project, name, weight, secs, log):
    return subprocess.Popen(AQ + ["run", "-p", project, "-n", name, "-w", str(weight), "--", sys.executable, "-c",
                                  f"import time;open({str(log)!r},'a').write('{name}+\\n');time.sleep({secs});open({str(log)!r},'a').write('{name}-\\n')"],
                            env=env(tmp), stderr=subprocess.DEVNULL)


def test_fifo_and_project_lock(tmp_path):
    log = tmp_path / "log"
    a = start(tmp_path, "A", "a1", 1, 2, log); time.sleep(0.5)
    b = start(tmp_path, "B", "b1", 1, 1, log); time.sleep(0.5)
    c = start(tmp_path, "A", "a2", 1, 1, log)
    for p in (a, b, c): assert p.wait(timeout=60) == 0
    seq = log.read_text().split()
    # b1 (other project) must not overlap a1; a2 waits behind b1 (strict FIFO)
    assert seq == ["a1+", "a1-", "b1+", "b1-", "a2+", "a2-"]


def test_weights_share_capacity(tmp_path):
    log = tmp_path / "log"
    ps = [start(tmp_path, "A", f"j{i}", 2, 7, log) for i in range(2)]
    for p in ps: assert p.wait(timeout=60) == 0
    seq = log.read_text().split()
    assert sorted(seq[:2]) == ["j0+", "j1+"]  # both weight-2 jobs started before either ended (they share capacity 4)


def test_exit_code_passthrough(tmp_path):
    r = subprocess.run(AQ + ["run", "-p", "A", "--", sys.executable, "-c", "raise SystemExit(7)"], env=env(tmp_path), stderr=subprocess.DEVNULL)
    assert r.returncode == 7


def test_status_json(tmp_path):
    out = subprocess.run(AQ + ["status", "--json"], env=env(tmp_path), capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    assert d["capacity"]["total"] == 4 and d["jobs"] == [] and d["cores"] >= 1


def test_install_idempotent_and_uninstall(tmp_path):
    e = env(tmp_path)
    for _ in range(2): subprocess.run(AQ + ["install", "claude", "--block"], env=e, check=True, capture_output=True)
    s = json.loads((tmp_path / "home/.claude/settings.json").read_text())
    assert len(s["hooks"]["SessionStart"]) == 1 and len(s["hooks"]["PreToolUse"]) == 1
    subprocess.run(AQ + ["install", "--uninstall", "claude"], env=e, check=True, capture_output=True)
    assert "hooks" not in json.loads((tmp_path / "home/.claude/settings.json").read_text())


def hook(cmd):
    p = subprocess.run([sys.executable, str(ROOT / "agent_job_queue/hooks/pre_bash.py")],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True)
    return "deny" in p.stdout


def test_block_hook_patterns():
    assert hook("node render.mjs video out.mp4 30 0 8")
    assert hook("ffmpeg -i a.mp4 -c:v libx264 b.mp4")
    assert not hook("aq run -p x -- node render.mjs video out.mp4 30 0 8")
    assert not hook("ffmpeg -ss 3 -i a.mp4 -frames:v 1 x.png")
    assert not hook('git commit -m "fix whisper render.mjs video"')
    assert not hook("ls -la")
