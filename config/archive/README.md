# Archived limit sets

Kept as records only. **The station can't load these:** both contain the phase steps (3060, 6060,
4060), which were removed by D-056, and the plan compiler refuses limits for steps that don't exist.

| File | What it was | Why it's kept |
|---|---|---|
| `TP-001_limits_v0.yaml` | **L-v0**, the first limit set, derived from DOE-1 with exploration math (`benchtop_testing/tools/derive_limits_v0.py`, D-019) | The first limits. The station's own code reproduced it exactly (D-050), proved by golden tests that were removed with the bench data (D-060). `tests/test_limit_sets.py` still checks that L-v1 has its values |
| `TP-001_limits_v1.yaml` | **L-v1**, L-v0's values frozen after the gauge run on the device (D-051) | The record of the freeze; `tests/test_limit_sets.py` pins what changed from L-v1 to L-v2 |

The live limit set is `config/TP-001_limits_v2.yaml` (L-v2, D-056), named in `config/settings.yaml`.
