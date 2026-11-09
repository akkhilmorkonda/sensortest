"""What the operator is told about a failed sensor (TP-001 §8, D-026). Shared by GUI and CLI."""

from sensortest.core.models import FaultFamily, StepResult

_MESSAGES = {
    FaultFamily.STUCK: "Stuck output",
    FaultFamily.GLITCH: "Glitches",
    FaultFamily.FREQUENCY: "Wrong frequency",
    FaultFamily.OFFSET: "Offset out of range",
    FaultFamily.RECOVERY: "Did not recover",
}


def failure_message(result: StepResult) -> str:
    """Plain-language message for a sensor's primary failure."""
    fam = result.family
    low_side = result.value is not None and result.low is not None and result.value < result.low
    if fam is FaultFamily.NOISE:
        return "No signal" if low_side else "Noisy"
    if fam is FaultFamily.GAIN:
        return "Weak response" if low_side else "Response too strong"
    return _MESSAGES.get(fam, "Out of limits") if fam is not None else "Out of limits"
