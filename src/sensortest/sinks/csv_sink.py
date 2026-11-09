"""Local CSV records, one self-contained file per run (D-032, D-048).

    <output>/<YYYY-MM-DD>/<SERIAL>_<run_id>.csv       one row per step x sensor
    <output>/<YYYY-MM-DD>/<SERIAL>_<run_id>_raw.csv   every capture window, all 8 sensors

Every row repeats the run's fields (test plan, run ID, serial, time, station, sequence, versions,
outcome, error code), so files can be concatenated into one production table with no joins. The
metadata JSON (D-029, single line) is filled on the run-level rows (sensor = RUN) only. The run
ID keeps file names unique when the same serial is tested more than once (retries, audits).
"""

import csv
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sensortest.core.models import RUN, SENSORS, Result, RunRecord
from sensortest.sinks.registry import sink

FORMAT = "tp001-csv-v2"  # file layout version, recorded inside the metadata JSON
RUN_FIELDS = [
    "test_plan", "run_id", "serial", "timestamp", "station", "sequence", "plan_version", "limit_set",
    "app_version", "device_usb_serial", "outcome", "failed_sensors", "error_code", "attempt", "retest_of",
]  # fmt: skip
STEP_FIELDS = [
    "step_id", "step_name", "sensor", "capture", "value", "unit", "low", "high", "result",
    "not_tested_reason", "fault_family", "primary_failure",
]  # fmt: skip
FIELDS = [*RUN_FIELDS, *STEP_FIELDS, "metadata"]


@sink("csv")
class CsvSink:
    name = "csv"

    def __init__(self, options: Mapping[str, Any]) -> None:
        self.root = Path(str(options["data_dir"]))

    def write_run(self, record: RunRecord) -> None:
        day = self.root / record.timestamp[:10]
        day.mkdir(parents=True, exist_ok=True)
        stem = f"{record.dut_serial or 'NOSERIAL'}_{record.run_id}"
        raw = _write_raw(day / f"{stem}_raw.csv", record)
        metadata = json.dumps(
            {**record.metadata, "format": FORMAT, "raw": raw},
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        )
        failed = sum(v is Result.FAIL for v in record.sensor_verdicts.values())
        run = {
            "test_plan": record.test_plan,
            "run_id": record.run_id,
            "serial": record.dut_serial,
            "timestamp": record.timestamp,
            "station": record.station,
            "sequence": record.sequence,
            "plan_version": record.plan_version,
            "limit_set": record.limit_set,
            "app_version": record.app_version,
            "device_usb_serial": record.device_serial,
            "outcome": record.outcome.value,
            "failed_sensors": failed if record.sensor_verdicts else "",
            "error_code": record.error_code,
            "attempt": record.attempt,
            "retest_of": record.retest_of,
        }
        rows = [
            {
                **run,
                "step_id": r.step_id,
                "step_name": r.name,
                "sensor": r.sensor,
                "capture": r.capture,
                "value": "" if r.value is None else repr(r.value),
                "unit": r.unit,
                "low": "" if r.low is None else r.low,
                "high": "" if r.high is None else r.high,
                "result": r.result.value,
                "not_tested_reason": r.not_tested_reason,
                "fault_family": r.family.name.lower() if r.family else "",
                "primary_failure": r.primary_failure,
                "metadata": metadata if r.sensor == RUN else "",
            }
            for r in record.results
        ]
        _atomic_write(day / f"{stem}.csv", FIELDS, rows)


def _write_raw(path: Path, record: RunRecord) -> dict[str, str]:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["capture", "line", "t_host_s", *SENSORS])
        for cap, win in record.windows.items():
            for i, (t, row) in enumerate(zip(win.t_host, win.data, strict=True)):
                w.writerow([cap, i, f"{t:.6f}", *(f"{v:.2f}" for v in row)])
        f.flush()
        os.fsync(f.fileno())
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _atomic_write(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    """Write a temp file, then rename it into place: a reader never sees a half-written run."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
