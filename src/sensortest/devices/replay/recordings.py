"""Bench recordings of the device's serial stream (DOE-1 format), read one way everywhere (D-050).

A recording is a TSV (host time, repr of each raw line) plus a JSON with the commands sent and
their host times; a DOE manifest.csv lists the runs. The replay link plays recordings back, and
characterization re-cuts them into test windows. Both read them through this module, so the
station's replay and the limit derivation see exactly the same lines.

Parsing follows the bring-up rules (docs/device_protocol.md): the first captured line is dropped
(the port opens mid-line), unterminated lines are dropped, and a data line is exactly 8 numbers.
"""

import ast
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sensortest.devices.teensy.protocol import parse_data


@dataclass(frozen=True)
class Recording:
    path: Path
    t: np.ndarray  # host time of each data line (s from capture start), shape (n,)
    x: np.ndarray  # the data lines, shape (n, 8)
    commands: tuple[tuple[float, str], ...]  # (host time, command) in the order sent

    def after(self, k: int) -> int:
        """Index of the first data line that arrived after the k-th command."""
        return int(np.searchsorted(self.t, self.commands[k][0], side="right"))


@dataclass(frozen=True)
class ManifestRun:
    sweep: int
    pos: int
    tone_hz: int | None  # None for a control run (baseline only)
    tsv: Path


def read_recording(tsv: Path) -> Recording:
    sent = json.loads(tsv.with_suffix(".json").read_text())["sent"]
    t, x = [], []
    for row in tsv.read_text(encoding="utf-8").splitlines()[2:]:  # header, then a partial line
        ts, rep = row.split("\t", 1)
        raw = ast.literal_eval(rep)
        if not raw.endswith(b"\n"):
            continue
        values = parse_data(raw)
        if values is not None:
            t.append(float(ts))
            x.append(values)
    return Recording(
        tsv,
        np.array(t),
        np.array(x).reshape(-1, 8),
        tuple((float(s["t_s"]), str(s["cmd"])) for s in sent),
    )


def read_manifest(root: Path) -> list[ManifestRun]:
    """The runs that passed their capture check (status ok), in sweep / position order."""
    with (root / "manifest.csv").open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["status"] == "ok"]
    return [
        ManifestRun(
            int(r["sweep"]),
            int(r["pos"]),
            None if r["cmd"] == "control" else int(r["cmd"]),
            root / f"sweep{int(r['sweep']):02d}" / f"{r['file']}.tsv",
        )
        for r in sorted(rows, key=lambda r: (int(r["sweep"]), int(r["pos"])))
    ]
