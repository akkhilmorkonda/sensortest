"""The recorded Teensy (D-031, D-049): the DOE-1 captures played back through the same actuator and
sensor interfaces, so the whole station runs without hardware: development, CI, the end-to-end and
GUI tests, demos, and fault injection for every error path. Importing this package registers its link
and drivers.

    link.py         @link("replay"): serves recorded lines and answers commands like the firmware
    actuator.py     @actuator("replay"): the Teensy's commands and reply checks, on the recording
    sensor.py       @sensor("replay"): settle + window lines per capture, live chunks
    recordings.py   reads a bench recording folder (manifest + .tsv/.json); also used by the
                    limit derivation (sensortest.characterization)

It speaks the Teensy's protocol (devices/teensy/protocol.py), because it replays that device.
"""

from sensortest.devices.replay import actuator, link, sensor  # noqa: F401  (registers link and drivers)
