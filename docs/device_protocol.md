# Device protocol and connection notes

Measured on the preflashed Teensy during hardware bring-up (2026-11-06). Every statement here is backed by a raw capture in `benchtop_testing/01_bringup/`.

The capture tools are `benchtop_testing/tools/bringup_capture.py`, `bringup_inspect.py`, `bringup_segments.py` and `bringup_timeline.py`.

Where the measured behavior differs from the brief, **the measurement wins** and the difference is flagged with ⚠.

To explore the device by hand, run `python benchtop_testing/tools/serial_terminal.py`. It auto-detects the Teensy, shows the device's replies, keeps data lines hidden until you ask for them, and can switch terminators. Type `:help` inside it.

## Connecting

| Item | Value |
|---|---|
| USB ID | VID:PID `16C0:0483` (PJRC Teensy USB Serial). Use this to auto-detect the port |
| Port seen | `COM4` on Windows. Expect `/dev/ttyACM*` on Linux and `/dev/cu.usbmodem*` on macOS (not yet verified on those) |
| Baud | Ignored by the Teensy (native USB). Use 115200. **Never 134: that baud rate starts the bootloader and puts the firmware at risk** |
| Stream on connect | Starts immediately. No handshake or command is needed |
| State across connections | **Persists.** Closing and reopening the port does not reset the device. A tone left running by one session is still running in the next |

**Connect procedure:**
1. Open the port.
2. Send `baseline\n` to put the device into a known state.
3. Discard partial lines until the first complete data line arrives.

**Shutdown procedure:** send `baseline\n` before closing the port, on normal stop, on abort, and on error.

## Data lines

```
-31.90, 144.10, 51.12, 274.35, 1333.45, -32.83, 10.85, 189.32, \r\n
```

- **Format:** 8 decimal numbers, each followed by `", "`, so there is a trailing comma, then `\r\n`. ASCII. Always 2 decimal places.
- **Units:** not stated by the device or the brief. Treated as arbitrary units (a.u.).
- **No sample counter or timestamp.** The host can't detect a dropped line except by timing.
- ⚠ **Sample rate: 150.0 lines/s. The brief's 100 Hz is wrong.**
  - It was measured as 150.01 lines/s by a least-squares fit on the host clock, the same in every capture (28,000+ lines). DOE-1 confirmed it: 150.011–150.013 in all 200 runs.
  - The app checks it on connect. More than 1% off stops the test as "test invalid" (D-008).
  - Excitation tones land exactly on the commanded frequency only when 150 Hz is assumed.
  - Nyquist is therefore 75 Hz.
- **DC offset: yes, different per channel.** Approximate idle means: ch1 −50, ch2 +135, ch3 +45, ch4 +376, ch5 +1349, ch6 0, ch7 +14, ch8 +215.
  - Under excitation, the tone oscillates around the same offset.
  - **AC coupling must be done in software.**

## Commands

Send ASCII, terminated with **`\n` (LF) only**.

| Command | Reply (arrives within ~1 ms, interleaved with data lines) | Effect |
|---|---|---|
| `response:N`, N = 1–19 | `N.00` | Excitation at N Hz, starting on the next data line (≤ 1 sample, ~7 ms) and continuing until changed. Zero-padding is fine: `response:05` behaves like `response:5` |
| `response:N`, N = 20–25 ⚠ | `response frequency invalid`, then `M.00`, where M is the last valid frequency | Excitation at **M Hz**, even when starting from baseline. The brief says 1–25 is valid, but the device accepts only 1–19 |
| `response:N` while already excited | same as above | Switches directly to the new frequency on the next line. No `baseline` is needed in between. ⚠ **The waveform can jump at the switch** (see below). An invalid N while running causes *no visible change*; only the rejection message shows it |
| `baseline` | *(none)* | Excitation off, from the next data line |
| anything else | `command not recognized` | No change |

**Changing frequency without `baseline`** (`benchtop_testing/01_bringup/*_direct_lf.tsv`: 7 → 22 → 15 → 3 Hz):

| Switch | Reply | Waveform at the switch |
|---|---|---|
| 7 → 22 (invalid) | `response frequency invalid`, `7.00` | Continues smoothly at 7 Hz |
| 7 → 15 | `15.00` | New frequency on the next line; it happened to stay close to continuous |
| 15 → 3 | `3.00` | **Jumps in one sample**: ch2 goes from −362 to +984, nearly full peak-to-peak |

- **Hypothesis:** the tone is computed as sin(2π·f·t) from a clock running since power-up. Changing f moves the angle instantly, so the tone jumps to an arbitrary point in its cycle. This also explains why tones start at a different point in the cycle each time. It's inferred from data, not read from firmware source.
- **Rule for the test:** after every command, **throw away a settle period (≥ 0.1–0.2 s)** before measuring. Otherwise the jump inflates the residual and spreads energy across the spectrum, and a healthy unit could fail.

**Terminators:**

| Terminator | `response:N` | `baseline` |
|---|---|---|
| `\n` | ✅ | ✅ |
| none | ✅ | ✅ |
| `\r\n` | ✅ | ❌ `command not recognized` |
| `\r` | ✅ | ❌ `command not recognized` |

The firmware appears to strip only `\n` and compare `baseline` exactly, so a leftover `\r` breaks the match. The number parser after `response:` tolerates it.

**Not tested:** `response:0`, negative or non-integer values, upper/lower case, and the state after a power cycle. The device was idle at the first connection of the day. The app only ever sends `baseline` and `response:1`–`19`.

## How the app talks to it (D-049)

The app has **two drivers for this one device**: an actuator driver (sends commands, checks replies) and a sensor driver (reads data lines). Both attach to one **serial link** that owns the port: it opens it once per run, reads every line on one thread, stamps each with the host time, and gives each line to the driver that wants it.

- **Lines with a comma are data** and go to the sensor driver. **Lines without a comma are replies** and go to the actuator driver.
- The link discards the partial first line after opening and refuses 134 baud.
- The actuator driver sends only `baseline` and `response:1`–`19`, terminated with LF, and checks `N.00` before it reports success. `baseline` gets no reply, so it counts as accepted when no `command not recognized` arrives within 50 ms.
- Code: `src/sensortest/devices/serial_link.py` (the generic serial link), and `devices/teensy/`: `actuator.py`, `sensor.py` and `protocol.py` (the command text, reply text and line format in one place). The recorded Teensy that replays DOE-1 without hardware is `devices/replay/`.
- In a real fixture the actuator and the sensor would be separate instruments, each on its own port, each port held under a device lock by one owner. The shared link exists because this fixture puts both on one Teensy.

## Telling lines apart (parser rule)

| Line | Meaning |
|---|---|
| Exactly 8 numeric fields | data sample |
| 1 numeric field | acknowledgement: the frequency now in effect. Check it against the commanded value |
| `response frequency invalid` | the command was rejected. Treat as an error |
| `command not recognized` | the command was rejected. Treat as an error |
| anything else, or a partial first line | discard and count |

## Early sensor observations

These are handed to characterization (section 04) and not judged here.

| Channel | Idle noise (approx. range) | Response to `response:N` |
|---|---|---|
| ch1 | −100 … 0 | ≈ 12 a.u. at N Hz, about 1% of the others |
| ch2 | 100 … 170 | ≈ 1000 a.u. at N Hz |
| ch3 | 25 … 65 | ≈ 900 a.u. at N Hz |
| ch4 | 55 … 703, by far the noisiest | ≈ 1200 a.u. at N Hz |
| ch5 | 1324 … 1374, large offset | ≈ 800 a.u. at N Hz |
| ch6 | −43 … 47 | ≈ 930 a.u. at N Hz |
| ch7 | −1 … 29 | ≈ 1020 a.u. at N Hz |
| ch8 | 175 … 255 | ≈ 890 a.u. at **2N Hz**, double the commanded frequency |

Idle noise on every channel looks uniformly distributed: each std ≈ range/√12. Amplitudes were the same across 1–19 Hz in the sweep.

### Signal model (checked against the 10 s sweep, `benchtop_testing/02_sweep_1-25hz/`)

Each value is **offset + uniform noise + a scaled copy of one shared tone**.

- **Noise:** each channel's value is its lowest value + k × step, with k a random integer from 0 to 999. That gives exactly 1,000 distinct values per channel, and an excess kurtosis of −1.20, the signature of a uniform distribution.
- **Offset:** unchanged when the tone starts, to within a few a.u.
- **Phase:** a least-squares sine fit puts ch2–ch7 at 0.0° from ch7 and ch1 within ±7°. They are all scaled copies of one tone.
- **Extra noise while excited:** on the clean channels, the residual after removing the sine grows by about 1% of the tone amplitude. For example, ch7 goes from 8.6 at baseline to about 14 while excited. **DOE-1 (190 runs per channel) resolved it into two parts:**
  - **1.44% rms amplitude jitter per sample**, independent on each channel;
  - **86 µs rms timing jitter per sample**, shared by all channels, consistent with jitter on the shared sample clock.

  So the residual std while excited is √(σ² + A²·(σ_a² + (2π·f·σ_t)²)/2): it grows with amplitude and, slowly, with frequency. Details are in `benchtop_testing/03_doe1_repeatability/analysis/report.md`.
- **Repeatability (DOE-1):** run to run, every metric scatters exactly as this noise predicts. There is no drift over 101 min, no carryover between frequencies, and recovery is immediate after `baseline`. Phase between channels is 0° ± 0.04–0.06°.
- **ch8 is a pure sine at 2N:**
  - no 4N harmonic;
  - no shift in offset.

  So it isn't a squared or rectified waveform. Its amplitude and noise look healthy, which means only a frequency check catches it.
- **ch1's small response is real:** about 10 a.u., at the commanded frequency, in phase with the other channels, at every N. It sits below ch1's own noise (std 29), at roughly −12 dB.

### Tone phase jump at the microsecond counter wrap (found 2026-11-08, D-051)

**About every 71.6 minutes of power-on time, the tone skips forward in its cycle in one sample, on every channel at once.** Seen once in the L-v1 gauge run: at 10 Hz the healthy channels jumped +117.5° to +118.2° and ch8 (at 20 Hz) −125.2°, in the same sample.
- **Most likely cause (inferred from the data; the firmware source isn't available):** the tone is computed as sin(2π·f·t) with t from the Teensy's 32-bit microsecond counter, which wraps every 2³² µs = 4,294.967296 s (71 min 35 s). The jump is then −360° × frac(f × 4,294.967296): +58.9° at 5 Hz, +117.7° at 10 Hz, +176.6° at 15 Hz, −124.5° at 20 Hz. The 10 Hz and 20 Hz predictions matched to within 1°.
- **It also explains** why tones start at a different point in their cycle every time (the clock behind them never resets).
- **Effect on a test:** a window containing the jump holds two sines that disagree, so the one-sine amplitude reads low (cos(θ/2) at worst, for a jump mid-window). Offset, noise and frequency are unchanged, and so is the phase between channels.
- **How the station handles it:** from D-052 to D-058, method M10 checked every tone window and a jump made the run a retryable ERROR, E211. **Since D-058 the station assumes the tone never jumps**, so a jump inside a tone window reads as low amplitude and can falsely fail a healthy channel (TP-001 §16 caveat 6). The device still does this. Re-measure the gauge-run case with `benchtop_testing/tools/phase_jump.py`.
