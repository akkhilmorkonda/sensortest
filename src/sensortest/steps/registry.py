"""The metric plugin point (D-033). Adding a test = a function here + an entry in the data files.

@metric("BL_OFFSET")
def bl_offset(window: Window, ctx: MetricContext) -> np.ndarray:  # shape (8,)
    return window.data.mean(axis=0)
"""

from sensortest.core.ports import MetricFn
from sensortest.core.registry import Registry

METRICS: Registry[MetricFn] = Registry("metric")
metric = METRICS.register
