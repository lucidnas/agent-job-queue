# AGENTS.md

- `agent_job_queue/cli.py` is the whole tool; it's stdlib only, so keep it that way.
- Test against a throwaway queue: `AQ_HOME=$(mktemp -d) python3 -m agent_job_queue ...`. The tests also set `HOME`, so `aq install` never touches your real `~/.claude`.
- Run tests with `python3 -m pytest -q`.
