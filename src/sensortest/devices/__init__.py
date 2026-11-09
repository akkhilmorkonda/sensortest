"""Hardware abstraction (PRD §6.2, D-037, D-049): one folder per device, generic plumbing at the top.

    manager.py       the device manager: which roles are connected; builds each run's session
    registry.py      the plugin points: @link, @actuator, @sensor
    serial_link.py   @link("serial"): one line-based serial port with one owner (any device)
    linequeue.py     the per-driver line queue a link fills
    taps.py          what drivers report for the live views (sample chunks, traffic)
    teensy/          the real device: protocol, actuator driver, sensor driver
    replay/          the recorded Teensy: link, drivers, and the bench recording reader

A new instrument is a new folder with its drivers (and a link, if it needs a new kind of
connection), plus one import in app/wiring.py.
"""
