"""DOE-1 runner: repeatability across 1-19 Hz (D-012, section 04).

Runs 10 sweeps. Each sweep is the 19 valid frequencies plus one baseline-only
control run, in a shuffled order. One run is a single 30 s capture:

    0.1 s  baseline        (10 s pre-baseline window)
    10.1 s response:XX     (10 s response window)      control: nothing sent
    20.1 s baseline        (10 s recovery window)

Every run is checked right after it finishes. A failed run is retried once, then
logged as a gap. Every attempt gets a row in manifest.csv.

Stop rules (the DOE aborts):
  * two consecutive runs fail their check (after the retry),
  * a serial error, or the device disappears,
  * the line rate is off by more than 1 %,
  * any ack that does not match the command sent.

Firmware safety: captures go through bringup_capture.capture(), which refuses
134 baud. Only `baseline` and `response:1`..`19` are ever sent. The device is
left in `baseline` on exit, including on abort and Ctrl-C.

Usage:
  python -u benchtop_testing/tools/doe_run.py --dry-run              # print the schedule, no device
  python -u benchtop_testing/tools/doe_run.py --precheck-only        # validity check only
  python -u benchtop_testing/tools/doe_run.py                        # precheck, then the full DOE
  python -u benchtop_testing/tools/doe_run.py --resume               # continue an interrupted DOE
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
BENCH = Path(__file__).resolve().parents[1]  # benchtop_testing/; defaults work from any cwd
from bringup_capture import capture                    # noqa: E402
from bringup_inspect import load                       # noqa: E402
from serial_terminal import classify, find_teensy      # noqa: E402

FREQS = list(range(1, 20))          # 20-25 are rejected by the device (D-009)
CONTROL = "control"
RUN_SECONDS = 30.3
SCRIPT_TONE = "0.1=baseline;10.1=response:{f};20.1=baseline"
SCRIPT_CONTROL = "0.1=baseline"
NOMINAL_RATE = 150.0
RATE_TOL = 0.5                      # run fails its check outside 150 +/- 0.5
RATE_ABORT = 0.01                   # DOE aborts if off by more than 1 %
LINES_TOL = 0.02                    # data-line count within 2 % of rate x duration
SETTLE_S = 0.5                      # skipped at the start of each window
CH7 = 6
CH7_NOMINAL = 1019.3
MANIFEST_FIELDS = ["sweep", "pos", "cmd", "attempt", "file", "started", "n_data",
                   "rate", "acks", "errors", "ch7_amp", "status", "reason"]


class Abort(Exception):
    """A stop rule fired."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schedule(seed: int, sweeps: int) -> list[tuple[int, int, object]]:
    """[(sweep, pos, cmd)], cmd = frequency (int) or CONTROL; positions 1-based."""
    rng = random.Random(seed)
    out = []
    for s in range(1, sweeps + 1):
        order: list[object] = FREQS.copy()
        rng.shuffle(order)
        order.insert(rng.randrange(len(order) + 1), CONTROL)
        out += [(s, i + 1, c) for i, c in enumerate(order)]
    return out


def sine_amp(y: np.ndarray, f: float, fs: float) -> float:
    t = np.arange(len(y)) / fs
    M = np.column_stack([np.ones_like(t), np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)])
    c, *_ = np.linalg.lstsq(M, y, rcond=None)
    return float(np.hypot(c[1], c[2]))


def check(base: Path, cmd, seconds: float) -> dict:
    """Validity check for one capture. Returns the manifest fields it fills."""
    rows = load(base.with_suffix(".tsv"))
    sent = json.loads(base.with_suffix(".json").read_text())["sent"]
    # Skip the first line (may be partial after reset_input_buffer) and any
    # unterminated last line: a partial data line can look like an ack.
    rows = [r for r in rows[1:] if r[1].endswith(b"\n")]
    data_t, data_x, acks, errors, other = [], [], [], [], 0
    for t, line in rows:
        kind, val = classify(line)
        if kind == "data":
            data_t.append(t)
            data_x.append(val)
        elif kind == "ack":
            acks.append(val)
        elif "invalid" in val or "not recognized" in val:
            errors.append(val)
        else:
            other += 1
    t = np.array(data_t)
    x = np.array(data_x)
    rate = float(np.polyfit(t, np.arange(len(t)), 1)[0]) if len(t) > 100 else 0.0

    ch7_amp = float("nan")
    if cmd != CONTROL and len(sent) >= 2 and rate > 0:
        t_on, t_off = sent[1]["t_s"], sent[2]["t_s"] if len(sent) > 2 else t[-1]
        m = (t > t_on + SETTLE_S) & (t < t_off)
        if m.sum() > 100:
            ch7_amp = sine_amp(x[m, CH7], float(cmd), rate)

    reasons = []
    expected_acks = [] if cmd == CONTROL else [float(cmd)]
    if abs(rate - NOMINAL_RATE) > RATE_TOL:
        reasons.append(f"rate {rate:.2f}")
    expected_n = NOMINAL_RATE * seconds
    if abs(len(t) - expected_n) > LINES_TOL * expected_n:
        reasons.append(f"{len(t)} data lines (expected ~{expected_n:.0f})")
    if acks != expected_acks:
        reasons.append(f"acks {acks} (expected {expected_acks})")
    if errors:
        reasons.append(f"device errors {errors}")
    if other:
        reasons.append(f"{other} unparsed lines")
    return {
        "n_data": len(t), "rate": f"{rate:.3f}", "acks": " ".join(f"{a:.2f}" for a in acks),
        "errors": " | ".join(errors), "ch7_amp": f"{ch7_amp:.2f}",
        "status": "ok" if not reasons else "fail", "reason": "; ".join(reasons),
        "_rate": rate, "_acks": acks, "_ch7": ch7_amp,
    }


def stop_rules_single(res: dict, cmd) -> None:
    if res["_rate"] and abs(res["_rate"] / NOMINAL_RATE - 1) > RATE_ABORT:
        raise Abort(f"line rate {res['_rate']:.2f} is off by more than 1 %")
    if cmd != CONTROL and res["_acks"] and res["_acks"] != [float(cmd)]:
        raise Abort(f"ack mismatch: sent response:{cmd}, got {res['_acks']}")
    if cmd == CONTROL and res["_acks"]:
        raise Abort(f"control run produced acks {res['_acks']} without a response command")


def send_baseline(port: str) -> None:
    try:
        with serial.Serial(port, baudrate=115200, timeout=0.2) as ser:
            ser.write(b"baseline\n")
            ser.flush()
    except serial.SerialException as e:
        print(f"WARNING: could not send baseline on exit: {e}", flush=True)


def precheck(port: str, out: Path) -> dict:
    """10 s response:10 capture; ack 10.00, rate 150 +/- 0.5, ch7 within 1 % of 1019."""
    seconds = 10.3
    script = [(0.1, "baseline"), (2.1, "response:10"), (10.1, "baseline")]
    base = capture(port, 115200, seconds, None, "\n", 0.0, out / "precheck", "precheck_r10", script)
    res = check(base, 10, seconds)
    problems = [] if res["status"] == "ok" else [res["reason"]]
    if not abs(res["_ch7"] / CH7_NOMINAL - 1) <= 0.01:
        problems.append(f"ch7 amplitude {res['_ch7']:.1f} not within 1 % of {CH7_NOMINAL}")
    res["file"] = base.name
    res["problems"] = problems
    print(f"precheck: acks [{res['acks']}]  rate {res['rate']}  data {res['n_data']}  "
          f"ch7 {res['ch7_amp']}  -> {'PASS' if not problems else 'FAIL: ' + '; '.join(problems)}",
          flush=True)
    return res


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default=None, help="default: auto-detect the Teensy by USB ID")
    p.add_argument("--out", type=Path, default=BENCH / "03_doe1_repeatability")
    p.add_argument("--sweeps", type=int, default=10)
    p.add_argument("--seed", type=int, default=None, help="default: from the clock, recorded")
    p.add_argument("--dry-run", action="store_true", help="print the schedule and exit")
    p.add_argument("--precheck-only", action="store_true")
    p.add_argument("--resume", action="store_true", help="skip runs already ok/gap in the manifest")
    a = p.parse_args()

    info_path = a.out / "run_info.json"
    manifest = a.out / "manifest.csv"
    done: set[tuple[int, int]] = set()
    if a.resume:
        info = json.loads(info_path.read_text())
        seed, sweeps = info["seed"], info["sweeps"]
        with manifest.open(newline="") as f:
            done = {(int(r["sweep"]), int(r["pos"])) for r in csv.DictReader(f) if r["status"] in ("ok", "gap")}
    else:
        seed = a.seed if a.seed is not None else int(time.time())
        sweeps = a.sweeps
    plan = schedule(seed, sweeps)

    if a.dry_run:
        print(f"seed {seed}: {len(plan)} runs, ~{len(plan) * (RUN_SECONDS + 0.7) / 60:.0f} min")
        for s in range(1, sweeps + 1):
            print(f"  sweep {s:2d}: " + " ".join(str(c) if c != CONTROL else "C" for ss, _, c in plan if ss == s))
        return

    port = a.port or find_teensy()
    if not port:
        sys.exit("No Teensy (USB 16C0:0483) found. Use --port.")
    a.out.mkdir(parents=True, exist_ok=True)

    if not a.resume:
        if manifest.exists():
            sys.exit(f"{manifest} already exists. Use --resume, or a different --out.")
        pre = precheck(port, a.out)
        info = {
            "doe": "DOE-1 repeatability 1-19 Hz (D-012)", "seed": seed, "sweeps": sweeps,
            "runs": len(plan), "run_seconds": RUN_SECONDS, "script_tone": SCRIPT_TONE,
            "script_control": SCRIPT_CONTROL, "port": port, "started": datetime.now().isoformat(timespec="seconds"),
            "python": platform.python_version(), "pyserial": serial.__version__,
            "sha256": {n: sha256(Path(__file__).resolve().parent / n)
                       for n in ("doe_run.py", "bringup_capture.py", "bringup_inspect.py", "serial_terminal.py")},
            "precheck": {k: v for k, v in pre.items() if not k.startswith("_")},
        }
        info_path.write_text(json.dumps(info, indent=2))
        if pre["problems"]:
            send_baseline(port)
            sys.exit("Precheck failed; DOE not started.")
        if a.precheck_only:
            send_baseline(port)
            return
        with manifest.open("w", newline="") as f:
            csv.DictWriter(f, MANIFEST_FIELDS).writeheader()

    todo = [r for r in plan if (r[0], r[1]) not in done]
    print(f"DOE-1: seed {seed}, {len(todo)} of {len(plan)} runs to do, "
          f"~{len(todo) * (RUN_SECONDS + 0.7) / 60:.0f} min", flush=True)
    consecutive_fail = 0
    t_start = time.time()
    try:
        for i, (s, pos, cmd) in enumerate(todo, 1):
            script_str = SCRIPT_CONTROL if cmd == CONTROL else SCRIPT_TONE.format(f=cmd)
            script = [(float(tt), c) for tt, c in (it.split("=", 1) for it in script_str.split(";"))]
            label = f"s{s:02d}p{pos:02d}_" + ("ctrl" if cmd == CONTROL else f"r{cmd:02d}")
            for attempt in (1, 2):
                started = datetime.now().isoformat(timespec="seconds")
                try:
                    base = capture(port, 115200, RUN_SECONDS, None, "\n", 0.0,
                                   a.out / f"sweep{s:02d}", label, script)
                except serial.SerialException as e:
                    row = {"sweep": s, "pos": pos, "cmd": cmd, "attempt": attempt, "started": started,
                           "status": "serial_error", "reason": str(e)}
                    with manifest.open("a", newline="") as f:
                        csv.DictWriter(f, MANIFEST_FIELDS).writerow(row)
                    raise Abort(f"serial error: {e}")
                res = check(base, cmd, RUN_SECONDS)
                if res["status"] == "fail" and attempt == 2:
                    res["status"] = "gap"
                row = {k: v for k, v in res.items() if not k.startswith("_")}
                row.update(sweep=s, pos=pos, cmd=cmd, attempt=attempt, file=base.name, started=started)
                with manifest.open("a", newline="") as f:
                    csv.DictWriter(f, MANIFEST_FIELDS).writerow(row)
                elapsed = (time.time() - t_start) / 60
                print(f"[{i:3d}/{len(todo)}] {elapsed:5.1f} min  sweep {s:2d} pos {pos:2d}  "
                      f"{'control' if cmd == CONTROL else f'response:{cmd:<2d}'}  try {attempt}  "
                      f"rate {res['rate']}  acks [{res['acks']}]  ch7 {res['ch7_amp']}  "
                      f"{res['status'].upper()}{'  ' + res['reason'] if res['reason'] else ''}", flush=True)
                stop_rules_single(res, cmd)
                if res["status"] == "ok":
                    consecutive_fail = 0
                    break
            else:
                consecutive_fail += 1
                if consecutive_fail >= 2:
                    raise Abort("two consecutive runs failed their check")
        print(f"DOE-1 COMPLETE: {len(todo)} runs in {(time.time() - t_start) / 60:.1f} min", flush=True)
    except Abort as e:
        print(f"DOE-1 ABORTED: {e}", flush=True)
        send_baseline(port)
        sys.exit(2)
    except KeyboardInterrupt:
        print("DOE-1 INTERRUPTED (resume with --resume)", flush=True)
        send_baseline(port)
        sys.exit(130)
    send_baseline(port)


if __name__ == "__main__":
    main()
