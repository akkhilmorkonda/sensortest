# Archived documents

Kept as the record of how decisions were reached. Not maintained; the live documents are one level up.

| Path | What it is | Decision |
|---|---|---|
| `gui_mockups/operator_layouts.html` | The three HTML layouts for the operator screen (A: step table, B: channel rows, C: step gauges), switchable between running, FAIL and ERROR | Layout A chosen (D-053) |
| `gui_mockups/operator_qt_mockup.py` and `qt_operator_*.png` | The PyQt render of layout A, made from a real PROD record before the build | Approved with no changes, then built (D-054) |

The built screen's screenshots are in `docs/gui_screens/`. The mockup script reads a gauge-run record
from `benchtop_testing/`, so it runs only where that data is present.
