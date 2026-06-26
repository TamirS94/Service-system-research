"""
Pipeline orchestrator — runs Stage 1 → Stage 2 → Stage 3 in sequence.

Each stage runs as its own subprocess (isolated memory; intermediate CSVs are
preserved on disk so a later stage can be resumed without rerunning earlier ones).
Output is teed to a timestamped log file so an overnight run can be reviewed later.

Run from the repo root (paths default to the root, where the data CSVs live):
    python src/run_pipeline.py                     # full run on raw_events_17_06.csv
    python src/run_pipeline.py --start-stage 3     # resume (reuse existing Stage 1/2 outputs)
    python src/run_pipeline.py --input <raw.csv> \
        --stage1-out <s1.csv> --exploded-out <exp.csv>   # custom (e.g. smoke test)
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

PYTHON = sys.executable
SRC_DIR = os.path.dirname(os.path.abspath(__file__))   # this script lives in src/
REPO_ROOT = os.path.dirname(SRC_DIR)                    # data CSVs live in the repo root
os.chdir(REPO_ROOT)                                     # run stages with root as cwd so they find the data

# Windows consoles default to a legacy codepage (e.g. cp1255) that can't encode the
# emoji the stages print. Force UTF-8 with replacement so logging never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# PYTHONUNBUFFERED so child prints appear immediately (not block-buffered through the pipe).
_CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")

STAGE1 = os.path.join(SRC_DIR, "stage_1_cleaning_and_unification.py")
STAGE2 = os.path.join(SRC_DIR, "stage_2_concurrencies_and_explode.py")
STAGE3 = os.path.join(SRC_DIR, "stage_3_creating_choicesets_from_exploded.py")


def run_stage(label, args, log):
    """Run one stage as a subprocess; stream output to console + log. Stop on failure."""
    header = f"\n{'=' * 60}\n{label}  —  {datetime.now():%Y-%m-%d %H:%M:%S}\n{'=' * 60}"
    print(header, flush=True)
    log.write(header + "\n")
    log.flush()

    t0 = time.time()
    # Stream the child's output live to BOTH terminal and log (line-by-line), so a
    # long-running stage shows progress in real time instead of only at the end.
    proc = subprocess.Popen(
        [PYTHON, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_CHILD_ENV,
        bufsize=1,
    )
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        log.write(line)
        log.flush()
    proc.wait()

    elapsed = time.time() - t0
    if proc.returncode != 0:
        msg = f"\n❌ {label} FAILED (exit {proc.returncode}) after {elapsed / 60:.1f} min — pipeline stopped."
        print(msg, flush=True)
        log.write(msg + "\n")
        log.flush()
        sys.exit(1)

    msg = f"✅ {label} done in {elapsed / 60:.1f} min"
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="raw_events_17_06.csv", help="raw events CSV for Stage 1")
    p.add_argument("--sessions", default="merged_session.csv", help="session-level windows for Stage 2")
    p.add_argument("--stage1-out", default="df_1_not_merged_2_merged.csv", help="Stage 1 output / Stage 2+3 input")
    p.add_argument("--exploded-out", default="df_exploded_all_data.csv", help="Stage 2 exploded output / Stage 3 input")
    p.add_argument("--before-third-out", default="before_third_stage_all_data.csv")
    p.add_argument("--start-stage", type=int, default=1, choices=[1, 2, 3],
                   help="skip earlier stages whose outputs already exist (e.g. 3 = run only Stage 3)")
    args = p.parse_args()

    log_name = f"pipeline_run_{datetime.now():%Y-%m-%d_%H%M%S}.log"
    print(f"Logging to {log_name}")

    with open(log_name, "w", encoding="utf-8") as log:
        log.write(f"PIPELINE START {datetime.now()}\nInput: {args.input}\n")
        log.flush()
        t_all = time.time()

        if args.start_stage <= 1:
            run_stage("STAGE 1 — cleaning & unification", [STAGE1, args.input, args.stage1_out], log)
        else:
            print("Skipping STAGE 1 (--start-stage)", flush=True)
        if args.start_stage <= 2:
            run_stage("STAGE 2 — concurrencies & explode", [STAGE2, args.stage1_out, args.exploded_out, args.before_third_out], log)
        else:
            print("Skipping STAGE 2 (--start-stage)", flush=True)
        run_stage("STAGE 3 — choice-set table", [STAGE3, args.stage1_out, args.exploded_out], log)

        total = time.time() - t_all
        done = f"\n🎉 PIPELINE COMPLETE in {total / 60:.1f} min  ({datetime.now():%Y-%m-%d %H:%M:%S})\nLog: {log_name}"
        print(done, flush=True)
        log.write(done + "\n")


if __name__ == "__main__":
    main()
