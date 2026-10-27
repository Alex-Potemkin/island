"""Run every check N times and print one summary line per run.

    python tests/run_all.py [N] [--no-mouse]

--no-mouse skips interactive_check (it moves the mouse and types for ~25 s).
On Linux run it under a window manager, e.g.:
    xvfb-run -a sh -c "openbox & sleep 1; python tests/run_all.py 2"
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["check_tools", "check_layout", "check_integrations", "check_calendar", "check_notes"]


def main():
    runs = int(next((a for a in sys.argv[1:] if a.isdigit()), "1"))
    tests = list(TESTS)
    if "--no-mouse" not in sys.argv:
        tests.append("interactive_check" if os.name == "nt" else "interactive_check_linux")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed = 0
    for i in range(1, runs + 1):
        parts = []
        for t in tests:
            env["ISLAND_HOME"] = tempfile.mkdtemp(prefix=f"island-{t}-")
            out = subprocess.run([sys.executable, os.path.join(HERE, t + ".py")], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=env, timeout=300).stdout
            m = re.search(r"(\d+)/(\d+) passed", out)
            ok = bool(m) and m.group(1) == m.group(2)
            failed += not ok
            parts.append(f"{t} {m.group(0) if m else 'CRASH'}")
            if not ok:
                for line in out.splitlines():
                    if line.startswith("FAIL") or "Traceback" in line or "Error" in line:
                        print("   ", t, line[:160])
        print(f"run {i}: " + " | ".join(parts), flush=True)
    print("ALL PASSED" if not failed else f"{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
