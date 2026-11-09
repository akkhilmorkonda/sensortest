"""The preflashed Teensy (D-049): both roles on its one USB serial port. Importing this package
registers its drivers.

    protocol.py   the command text, reply text and line format (docs/device_protocol.md)
    actuator.py   @actuator("teensy"): sends `baseline` / `response:N`, checks each reply
    sensor.py     @sensor("teensy"): counts data lines into windows, checks the stream

Both drivers attach to one generic serial link (devices/serial_link.py), which owns the port.
"""

from sensortest.devices.teensy import actuator, sensor  # noqa: F401  (registers the drivers)
