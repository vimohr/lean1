#!/usr/bin/env python3

import subprocess
import time
from datetime import datetime
from pathlib import Path

WEBSITE = "https://qiqc-op.com/problem/op_e432fc5ff73e63be/"
EMAIL = "vimohr@ethz.ch"

PROMPT = f"""
You are a quantum information researcher. Your task is to prove or disprove this problem {WEBSITE}. To do this, please make extensive use of lean verification to certify the proofs. Only stop when you are done with the task. If you have no ideas for attacking the problem you are allowed to spawn a sub agent to help you, but make sure to never have more than 2 subagents at the same time. When you are done, please send an email to {EMAIL}.
"""

RESTART_DELAY = 3
LOG_FILE = Path("codex_runs.log")


def log(message: str):
    timestamp = datetime.now().isoformat(timespec="seconds")

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp}\t{message}\n")


def run_codex(run_number: int):
    print(f"\n=== Codex run #{run_number} ===")

    result = subprocess.run(
        [
            "codex",
            "exec",
            "--json",
            PROMPT,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    return result.returncode, result.stderr


def main():
    run_number = 0

    try:
        while True:
            run_number += 1

            start = datetime.now()
            returncode, stderr = run_codex(run_number)
            duration = datetime.now() - start

            reason = f"exit={returncode}, duration={duration}"

            if stderr:
                last_error = stderr.strip().splitlines()[-1]
                reason += f", stderr={last_error}"

            log(f"run #{run_number}: {reason}")

            print(f"Finished run #{run_number}: {reason}")
            print(f"Restarting in {RESTART_DELAY}s...")

            time.sleep(RESTART_DELAY)

    except KeyboardInterrupt:
        log("supervisor stopped by user")
        print("\nStopped.")


if __name__ == "__main__":
    main()