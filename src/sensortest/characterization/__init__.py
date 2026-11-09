"""Characterization (Part 1) in product code (D-004, D-050).

Turns DOE-1 bench recordings into test windows, a metric table, a limit set and expected verdicts,
using the station's own capture timing, metrics and evaluation. The math that sets a limit is the
math that checks it.

    recut      DOE-1 runs -> BL / T10 / REC windows, timed by the plan's captures
    table      one row per run x sensor, from the @metric functions
    limits     the D-019 rule: healthy range +/- 10 % + 3 x repeatability SD
    verdicts   each run through the engine's evaluation, as a PROD run

Used by benchtop_testing/tools/derive_limits.py on the bench recordings, and tested on synthetic
recordings (tests/test_synthetic.py; the golden tests on DOE-1 were removed with the data, D-060).
"""
