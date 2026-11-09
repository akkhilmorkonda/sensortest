# Sensor line test plan

| | |
|---|---|
| **Document** | TP-001 Sensor line test plan |
| **Version** | **v2.1** (D-058): the actuator's tone is assumed never to jump, so there is no stimulus check (E211, M10). **v2** (D-056): all channels are assumed in phase, so there is no phase step and no step depends on another; amplitude is fitted at the frequency the channel responds at; limit set **L-v2** (§16) |
| **Date** | 2026-11-08 |
| **Decisions** | D-002, D-003, D-004, D-007–D-052 (my decision log, kept outside the repository) |
| **Evidence** | Bring-up (`benchtop_testing/01_bringup/`), DOE-1 (`benchtop_testing/03_doe1_repeatability/`), the 04 note (pre-work B, DOE-1 §6–7), `docs/device_protocol.md` |

**Two sequences from one step library (D-028, D-040).** Every step is defined once in the **sensor test file**. The **settings file** defines PROD and ENG as explicit lists of step IDs. The operator view always runs PROD; the engineering view can choose either.

| Sequence | Used for | Captures | Test steps | Time per DUT |
|---|---|---|---|---|
| **TP-001-PROD** | **Every production unit** | BL, T10 | 2 validity + **4 limit steps per sensor** | **≈ 5.0 s** |
| **TP-001-ENG** | EVT/DVT builds, production audits, after any design or process change | BL, T5, T10, T15, REC | 2 validity + 14 limit steps per sensor | ≈ 10.5 s |

**What's in the sequence and what isn't (D-022, D-023, D-029):**
- **Test steps** can PASS, FAIL or ERROR, and appear in the sequence and on the UI:
  - two grouped validity steps (1000 CONNECT, 1900 RUN_INTEGRITY), with error codes;
  - one measured quantity per limit step.
- **Diagnostics** are measured but never judged. They are **not test steps**: they don't appear in the sequence or on the operator UI. They're saved in the run's `metadata` JSON (§12).

**Version path:**
- **v0 draft:** this document.
- **v0 limits (done, L-v0):** from DOE-1 raw data re-cut to this timing (D-019); `config/archive/TP-001_limits_v0.yaml`, derived by `benchtop_testing/tools/derive_limits_v0.py` (§16).
- **v1 (done 2026-11-08, L-v1):** recomputed with the measurement core (03): identical (D-050). A 20 + 20-run gauge check on the device: zero ERRORs and zero false fails; the added drift check failed as run because of one run hit by an actuator fault, and passed with that run excluded (D-051). Frozen as `config/TP-001_limits_v1.yaml` with L-v0's values.
- **v2 (2026-11-08, L-v2, D-056):** all channels are assumed in phase for this assignment, so the three phase steps are gone and no step depends on another. Amplitude is fitted at the frequency the channel actually responds at, so a channel at the wrong frequency (ch8) fails frequency only, not both. L-v2 = L-v1's values without phase; the amplitude high limit moves 1128.3 → 1128.5. Same verdicts and primary failures on all 190 DOE-1 runs
- **v2.1 (2026-11-08, D-058):** the actuator's tone is assumed never to jump, so the stimulus check (E211, method M10) is removed. Same limits (L-v2) and same verdicts; a real jump would again read as low amplitude (§16 caveat 6)

---

## 1. Scope

**Purpose:** decide, for each of the 8 sensors, whether it is fit to ship (PASS) or not (FAIL). The station also has to recognize when it can't make that decision reliably (ERROR).

**Device under test (DUT):**
- 8 sensor channels, each treated as a separate sensor (D-002), tested together in one run.
- They're excited by one shared actuator, commanded over USB serial.
- The device is the preflashed Teensy simulator; its firmware is never modified.
- Each DUT is a new USB connection.

**Equipment:** a host PC running the test app, a USB cable, and the Teensy (USB ID 16C0:0483).

**Out of scope:** see §14.

---

## 2. Definitions

| Term | Meaning |
|---|---|
| **a.u.** | Arbitrary units: the device's output numbers. The real units are unknown |
| **fs_nom** | **150 Hz**, the device's sample rate. Every fit and spectrum uses this fixed time base (D-025). The brief's 100 Hz is wrong (D-008) |
| **Line rate** | Measured on the host clock. A validity check only (148.5–151.5) |
| **Test step** | Something that can PASS, FAIL or ERROR. Either a **validity step** (failure → run ERROR) or a **limit step** (failure → that sensor FAILs). Limit steps measure one quantity each (D-022) |
| **Diagnostic** | A quantity measured from a capture and saved, but **never judged**. Not a test step: it isn't in the sequence or on the operator UI (D-029). Stored in the run's `metadata` JSON |
| **Capture** | A block of data collected after one command: **BL** baseline, **T5 / T10 / T15** tone at 5 / 10 / 15 Hz, **REC** recovery. Each capture feeds its test steps and its diagnostics |
| **Settle** | Data lines discarded after a command before measuring: **30 lines (0.2 s)** |
| **Window** | The lines measured: **300 lines (2 s)**, or 150 lines (1 s) for REC. Counted in lines, always a **multiple of 30** (D-027) |
| **Tone-present threshold** | **100 a.u.** One constant used four ways (D-030, D-056): device idle (E106), REC tone absent (5050), the frequency gate (6050), and the amplitude's choice of fit frequency (6040/3040/4040: a tone is present, so fit at its measured frequency). No-stimulus amplitude is ≤ 8 a.u. (ch4 ≤ 61); healthy tones are ≥ 795 |
| **Results** | Per test step and sensor: **PASS / FAIL / NOT TESTED** |
| **Verdicts** | Per sensor **PASS / FAIL**. Per run **ERROR** (a fault; no sensor verdicts) or **ABORTED** (the operator pressed Abort; safe-stated, sensors NOT TESTED, record written, D-038) |
| **Metric key** | The stable name used for a quantity, e.g. `BL_STUCK_RUN`. A diagnostic and the limit step it could be promoted to use the **same key**, so its history continues across a promotion |
| **Error code** | E1xx connection, E2xx during the run, E3xx configuration (§9). The `@BL`, `@T10`, … suffix names the capture |

---

## 3. How PROD was derived from ENG (D-028)

**Method:** start from the highest-resolution plan (ENG), then cut down to what is justified by the failure modes in the data, cycle time and false-fail risk. DOE-1 and bring-up stand in for an EVT/DVT data set.

**Rule for keeping a limit in PROD.** Both must hold:
1. **It catches a fault seen in the data, or it's the only step that would catch a plausible fault**, and
2. **it costs no test time and has negligible false-fail risk.**

Otherwise it becomes a **diagnostic** in PROD if its capture still runs, or **ENG-only** if its capture is dropped. A capture left with no limit steps is dropped from PROD.

**Coverage matrix:**

| Fault mode | Seen in data? | ENG limit steps | Also caught by | PROD decision |
|---|---|---|---|---|
| Offset out of range | **Yes, ch5** | 2040 | — | **Limit** 2040 |
| Noisy | **Yes, ch4** | 2050 | — | **Limit** 2050 |
| Dead or flat output | No | 2050 (min) | 6040 amplitude min | Keep: part of 2050, no extra step |
| Stuck output (fully) | No | 2060, 5080 | 2050 min (std = 0), 6040 min (A = 0) | `BL_STUCK_RUN` **diagnostic**; 5080 ENG-only |
| Stuck output (briefly) | No | 2060, 5080 | — | Diagnostic. The threshold also depends on fine quantization; a coarser real ADC repeats values naturally |
| Spikes, glitches | No (0 in 3,200 windows) | 2070, 5090 | — | `BL_OUTLIERS` **diagnostic**; 5090 ENG-only. The 2.5σ rule is safe only for uniform noise: Gaussian noise exceeds 2.5σ in ~1.2% of samples |
| Weak response / too strong | **Yes, ch1 and ch4** | 3040, 4040, 6040 | — | **Limit** 6040 |
| Wrong frequency | **Yes, ch8** | 3050, 4050, 6050 | — (since D-056 amplitude reads ch8's real response, ~890) | **Limit** 6050: names the root cause for the Pareto (ch8 ≠ ch1). Zero cost |
| Reversed polarity, delay, lost bandwidth | No | — | — | **Not covered since D-056:** all channels are assumed in phase for this assignment. Phase (6060, D-016) was the only check for these; a reversed or one-line-delayed channel passes amplitude and frequency (§16 caveat 7) |
| Frequency-dependent fault | No (flat within 0.06% over 1–19 Hz) | T5 + T15 steps | — | **One tone (T10) in PROD;** T5/T15 are ENG-only; saves 2.2 s |
| Doesn't recover after excitation | No (190/190 back by the second line) | 5050, 5060 | — | **REC is ENG-only;** saves 1.2 s. `baseline` is still sent at the end (D-010) |
| Distortion, interference, drift in a window | No | — (diagnostics in both) | — | Diagnostics (D-017, D-018) |

**Result:** every fault in the data is still caught in PROD; the expected verdicts are unchanged (§8).

**Caution:** "not seen" rests on 8 channels of one device. It shows the observed faults are constant, not how often rare defects occur. Dropped coverage is a diagnostic in PROD or covered by ENG audits, each with a promotion trigger (§15).

---

## 4. TP-001-PROD sequence (every unit)

```
OPEN PORT (115200 baud, never 134)
  BL   baseline     settle 30, window 300   → 1000 CONNECT, 2040, 2050          (+ BL diagnostics)
  T10  response:10  settle 30, window 300   → 6040, 6050                        (+ T10 diagnostics)
  END  baseline     read ~10 lines for a rejection (no measurement)
  1900 RUN_INTEGRITY (continuous; the first fault aborts → ERROR)
  VERDICT → operator screen;  record → database (§12)
```

| Capture | Command | Reply | Settle | Window | Whole cycles | Time |
|---|---|---|---|---|---|---|
| BL | `baseline\n` | none | 30 | 300 | — | 2.2 s |
| T10 | `response:10\n` | `10.00` | 30 | 300 | 20 at 10 Hz (ch8's 2f: 40) | 2.2 s |
| END | `baseline\n` | none | — | ~10 lines (rejection check only) | — | ≈ 0.1 s |
| | | | | | USB open/close (est.) | ≈ 0.5 s |
| | | | | | **Per DUT** | **≈ 5.0 s** |

**Why 10 Hz:** mid-band, 20 whole cycles in 300 lines. ch8's 2f falls at 20 Hz, far below 75 Hz. It also avoids the 1 Hz problems (D-015, D-028).

**Run to completion;** only an ERROR stops early (D-026).

---

## 5. TP-001-ENG sequence (full resolution)

```
  BL → T5 → T10 → T15 (tones switched directly, ascending) → REC → verdict
```

| Capture | Command | Reply | Settle | Window | Whole cycles | Time |
|---|---|---|---|---|---|---|
| BL | `baseline\n` | none | 30 | 300 | — | 2.2 s |
| T5 | `response:5\n` | `5.00` | 30 | 300 | 10 | 2.2 s |
| T10 | `response:10\n` | `10.00` | 30 | 300 | 20 | 2.2 s |
| T15 | `response:15\n` | `15.00` | 30 | 300 | 30 | 2.2 s |
| REC | `baseline\n` | none | 30 | 150 | 15 at 15 Hz | 1.2 s |
| | | | | | USB (est.) | ≈ 0.5 s |
| | | | | | **Per DUT** | **≈ 10.5 s** |

**When to run ENG:**
- every unit in EVT/DVT builds;
- after any design, firmware or process change;
- as a **production audit** on a sample of units (rate TBD, e.g. the first units of each lot plus periodic samples).

ENG results feed the promotion triggers (§15). T10 is in ENG too, so every PROD step has an identical ENG counterpart.

---

## 6. Measurement methods

Every step and diagnostic names one of these methods. The measurement core (03) implements exactly these, and the limits are computed with the same code (D-004).
- `x[n]`, n = 0…N−1, is one sensor's window.

| ID | Method | Definition |
|---|---|---|
| **M1** | Window extraction | After writing the command: discard the first 30 data lines, then take the next N data lines. Reply and message lines are handled by RUN_INTEGRITY |
| **M2** | Mean / std / peak-to-peak | mean = Σx/N; std = sample standard deviation (ddof = 1); p2p = max − min |
| **M3** | Sine fit | Least squares: x[n] ≈ c + a·sin(2πfn/fs_nom) + b·cos(2πfn/fs_nom). A = √(a² + b²); φ = atan2(b, a); fitted offset c; residual r = x − fit. **f is the commanded frequency**, except for the amplitude steps (6040, 3040, 4040, D-056): there f is the **measured dominant frequency (M4)** when its peak is ≥ 100 a.u. (a tone is present), else the commanded f. For a whole-cycle window, A at the commanded f equals the FFT value at that frequency. Diagnostics, 5050 and the stimulus check (M10) use the commanded f |
| **M4** | Dominant frequency | Hann window on x − mean, zero-padded ×8, FFT on the fs_nom time base. The largest peak above 0.5 Hz, refined by parabolic interpolation |
| **M5** | *(removed, D-056)* | Phase vs the median of the other channels. All channels are assumed in phase, so there is no phase step |
| **M6** | Spur | Hann-window FFT of the M3 residual; largest magnitude above 0.5 Hz and more than 1 Hz from f |
| **M7** | Longest identical run | Longest run of consecutive identical values |
| **M8** | Outlier count | Samples with \|x − mean\| > 2.5 × std. Safe for this simulator's uniform noise only; revisit for real hardware (§15) |
| **M9** | Line rate (validity only) | Least-squares slope of line index vs host arrival time |
| **M10** | *(removed, D-058)* | Stimulus step: the tone's phase step between 0.2 s blocks, voted across the channels carrying the tone. It existed only for the actuator's phase jump, which is now assumed never to happen |

---

## 7. Test steps and diagnostics

**Test step IDs:** 4 digits. The thousands digit is the block: 1 validity, 2 BL, 3 T5, 4 T15, 5 REC, 6 T10. Steps go up in tens.
- IDs are never reused.
- Retired: 1010–1060, 2010–2030, 3010–3030, 4010–4030, 5010–5040 (merged into 1000/1900); 2080, 3070–3110, 4070–4110, 5070, 5100, 5110, 6070–6110 (now diagnostics, D-029).

### 7.1 Validity steps (run-level; any failure → run ERROR)
| ID | Name | Checks (error codes, §9) | Value recorded | PROD | ENG | Ref |
|---|---|---|---|---|---|---|
| **1000** | **CONNECT** | Device found (E101); port opens (E102); `baseline` accepted (E103); BL line rate 148.5–151.5 (E104); BL lines all parse (E105); device idle: amplitude in BL at each tone frequency in the sequence **< 100 a.u.** on every sensor (E106) | BL line rate (Hz) | ✓ | ✓ | D-023, D-024 |
| **1900** | **RUN_INTEGRITY** | For each tone capture: reply exactly `N.00` within 0.5 s (E201–E203); no `command not recognized` incl. the final baseline (E204); line rate within 1% (E205); all lines parse (E206); *(The stimulus check E211 was removed, D-058: the tone is assumed never to jump.)* *(The ENG-only "actuator stopped" check E207 was dropped, D-057: a tone that doesn't stop shows as 5050 failures.)* | 0, or the first fault's code | ✓ | ✓ | D-009, D-010, D-023 |

Plan and limit-file checks (E301, E302) run before any run starts.

### 7.2 Limit steps
| ID | Name (metric key) | Capture | Tests | Method | Unit | Limit | Family (rank) | **PROD** | **ENG** | Expected here |
|---|---|---|---|---|---|---|---|---|---|---|
| **2040** | **BL_OFFSET** | BL | DC level at rest | M2 mean | a.u. | **−127.1 … 455.8** | offset (5) | ✓ | ✓ | **ch5 FAIL** |
| **2050** | **BL_NOISE_STD** | BL | Noise at rest (max: noisy; min: dead) | M2 std | a.u. | **5.6 … 33.9** | noise (4) | ✓ | ✓ | **ch4 FAIL** |
| 2060 | BL_STUCK_RUN | BL | Stuck output | M7 | samples | max **5** | stuck (1) | diagnostic | ✓ | — |
| 2070 | BL_OUTLIERS | BL | Spikes or glitches | M8 | count | max 0 | glitch (2) | diagnostic | ✓ | — |
| **6040** | **T10_AMPLITUDE** | T10 | Response strength (min: weak; max: excess gain) | M3 A at the response frequency (D-056) | a.u. | **712.8 … 1128.5** | gain (6) | ✓ | ✓ | **ch1, ch4 FAIL** (ch8 reads ~890: PASS) |
| **6050** | **T10_FREQUENCY** | T10 | Responds at the commanded frequency | M4 | Hz | **9.5 … 10.5** (10 ± 0.5); **judged only if the dominant peak ≥ 100 a.u.**, otherwise NOT TESTED (D-030) | frequency (3) | ✓ | ✓ | **ch8 FAIL** (20 Hz) |
| 3040 / 3050 | T5_AMPLITUDE / T5_FREQUENCY | T5 | As 6040–6050 at 5 Hz (frequency 4.5 … 5.5) | M3 / M4 | | as T10 | gain / frequency | — | ✓ | ch1, ch4 FAIL 3040; ch8 FAIL 3050 |
| 4040 / 4050 | T15_AMPLITUDE / T15_FREQUENCY | T15 | As 6040–6050 at 15 Hz (frequency 14.5 … 15.5) | M3 / M4 | | as T10 | gain / frequency | — | ✓ | ch1, ch4 FAIL 4040; ch8 FAIL 4050 |
| 5050 | REC_TONE_ABSENT | REC | This sensor no longer shows the 15 Hz tone (since D-057 also the only data check that the actuator stopped) | M3 A at 15 Hz | a.u. | max **100** | recovery (8) | — | ✓ | all PASS |
| 5060 | REC_OFFSET_SHIFT | REC | Returned to its own baseline level | M2 mean − 2040 | a.u. | **−60.5 … 61.7** (loose, see §16) | recovery (8) | — | ✓ | all PASS |
| 5080 | REC_STUCK_RUN | REC | Stuck output after excitation | M7 | samples | max **5** | stuck (1) | — | ✓ | all PASS |
| 5090 | REC_OUTLIERS | REC | Glitches after excitation | M8 | count | max 0 | glitch (2) | — | ✓ | all PASS |

✓ = limit step in that sequence; **diagnostic** = measured and saved in `metadata` but not a step; — = not run.

### 7.3 Diagnostics (not test steps; saved in `metadata`, never shown to the operator)
| Metric key | Capture | Measures | Method | Unit | PROD | ENG | Why measured |
|---|---|---|---|---|---|---|---|
| `BL_STUCK_RUN` | BL | Longest identical run | M7 | samples | ✓ | (limit step 2060) | Promotion trigger (§15) |
| `BL_OUTLIERS` | BL | Outlier count | M8 | count | ✓ | (limit step 2070) | Promotion trigger (§15) |
| `BL_P2P` | BL | Peak-to-peak | M2 | a.u. | ✓ | ✓ | Overlaps noise std; SPC context |
| `Txx_RESIDUAL` | each tone | Residual std after the sine fit (ddof = 3) | M3 | a.u. | ✓ (T10) | ✓ (T5/T10/T15) | Distortion or glitches during the tone (D-017) |
| `Txx_SPUR` | each tone | Largest spur | M6 | a.u. | ✓ | ✓ | Extra tone or interference (D-018) |
| `Txx_HALF_STABILITY` | each tone | A (2nd half) ÷ A (1st half) − 1 | M3 | % | ✓ | ✓ | Drift within a window |
| `Txx_OFFSET_SHIFT` | each tone | M3 c − BL_OFFSET | M3 | a.u. | ✓ | ✓ | DC shift under excitation (chart 4) |
| `Txx_SNR` | each tone | 20·log10((A/√2) / BL_NOISE_STD) | — | dB | ✓ | ✓ | Ratio of two limited metrics (D-011) |
| `REC_NOISE_STD` | REC | Noise after excitation | M2 | a.u. | — | ✓ | Demoted (D-026) |
| `REC_OFFSET`, `REC_P2P` | REC | Level and p2p after excitation | M2 | a.u. | — | ✓ | Recovery context |

`Txx` = T5, T10 or T15. A diagnostic that depends on a failed or missing value (e.g. SNR when BL errored) is saved as `null` with a reason.

**Counts:**

| | Validity steps (per run) | Limit steps (per sensor) | Result rows per run | Diagnostics per sensor (in `metadata`) |
|---|---|---|---|---|
| **PROD** | 2 | **5** | 2 + 5 × 8 = **42** | 8 |
| **ENG** | 2 | 17 | 2 + 17 × 8 = 138 | 19 |

---

## 8. Results, verdicts and priorities

**The DUT is one sensor with 8 output channels (D-002 reversed, D-053).** In this section "sensor" means **channel** (records keep the `sensor` column for the channel ID). **DUT verdict:** PASS only if all 8 channels PASS; otherwise FAIL. This is the run outcome the record already carries.

1. **ERROR:** if 1000 or 1900 fails, the run stops at once.
   - The app sends `baseline\n` (D-010).
   - Steps not yet evaluated are NOT TESTED and cite the error code. Their diagnostics are `null` with the same reason.
   - No sensor verdicts are issued.
2. **Independent steps (D-056):** every step is judged on its own channel's data alone; no step waits on another or on other channels. The one gate:
   - **Frequency** (6050) is NOT TESTED if the sensor's dominant peak is below 100 a.u.: with no tone, the "dominant frequency" is just the biggest noise peak (D-030).
3. **Channel verdict:** **PASS** if every limit step in the sequence is PASS or NOT TESTED (frequency with no tone); otherwise **FAIL**. Diagnostics never affect a verdict.
4. **Primary failure (D-026):** the failed step whose fault family has the lowest rank. Ties go to execution order.

| Rank | Family | Operator message | In PROD |
|---|---|---|---|
| 1 | stuck | Stuck output | (diagnostic only) |
| 2 | glitch | Glitches | (diagnostic only) |
| 3 | frequency | **Wrong frequency** | 6050 |
| 4 | noise | **Noisy** / **No signal** (below min) | 2050 |
| 5 | offset | **Offset out of range** | 2040 |
| 6 | gain | **Weak response** / **Response too strong** | 6040 |
| ~~7~~ | ~~timing~~ | *(removed with phase, D-056; the ranks keep their numbers)* | — |
| 8 | recovery | Did not recover | (ENG only) |

5. **No automatic retest on FAIL. ERROR:** at most one operator retry, for retryable codes only; every attempt is logged and linked (D-026).

**Expected PROD result on this device (sanity check for v0 limits):**

| Sensor | Verdict | Operator sees | All failing steps |
|---|---|---|---|
| ch1 | FAIL | Weak response | 6040 (6050 NOT TESTED: no tone) |
| ch2, ch3, ch6, ch7 | PASS | — | — |
| ch4 | FAIL | Noisy | 2050; 6040 (high) |
| ch5 | FAIL | Offset out of range | 2040 |
| ch8 | FAIL | Wrong frequency | 6050 only: since D-056 amplitude reads its real response at 20 Hz (~890), so one fault is one failed step |

**Verified with L-v0** on 190 simulated PROD runs (DOE-1 re-cut): every sensor got this exact verdict and operator message in all 190 runs (§16). L-v1 has the same values, and the 40-run gauge run on the device gave the same verdicts (D-051). L-v2 (D-056) gives the same verdicts and primary failures on all 190 runs; ch8's failed steps go from 6040 + 6050 to 6050. The limits are partly derived knowing which sensors are faulty (D-019), so this is a consistency check, not independent proof.

---

## 9. Error codes

| Code | Step | Meaning | Retryable | Operator action |
|---|---|---|---|---|
| E101 | 1000 | Device not found (USB ID 16C0:0483) | yes | Reconnect the DUT, retry once |
| E102 | 1000 | Port won't open (in use or busy) | yes | Close other programs or reconnect, retry once |
| E103 | 1000 | `baseline` rejected | no | Call engineering (D-007) |
| E104 | 1000 | Line rate outside 148.5–151.5 | no | Call engineering (D-008) |
| E105 | 1000 | Unreadable data in BL | yes | Retry once |
| E106 | 1000 | Device not idle after `baseline` | no | Call engineering (D-010) |
| E201@Tn | 1900 | Wrong reply value (e.g. `19.00` to `response:10`) | no | Call engineering (D-009) |
| E202@Tn | 1900 | `response frequency invalid` | no | Call engineering (D-009) |
| E203@Tn | 1900 | No reply within 0.5 s | no | Call engineering |
| E204@… | 1900 | `command not recognized` (incl. the final baseline, `@END`) | no | Call engineering (D-007) |
| E205@… | 1900 | Dropped lines (line rate outside 1%) | yes | Retry once |
| E206@… | 1900 | Unreadable lines | yes | Retry once |
| ~~E207@REC~~ | — | *Dropped (D-057); the number is never reused.* It was "actuator didn't stop" (ENG only). In ENG, a tone that keeps running now fails 5050 REC_TONE_ABSENT on each channel carrying it | — | — |
| E301 | pre-run | Test plan invalid (e.g. a tone outside 1–19) | no | Call engineering (D-009) |
| E302 | pre-run | Limit file missing or version mismatch | no | Call engineering |
| E208@… | 1900 | Device stopped sending data (no line for 0.5 s) | no | Call engineering (D-036) |
| E209@… | 1900 | Device lost mid-run (USB unplugged) | yes | Reconnect the DUT, retry once (D-036) |
| E210 | 1900 | Whole-run watchdog exceeded (expected run time × 2) | no | Call engineering (D-036) |
| ~~E211@Tn~~ | — | *Removed (D-058); the number is never reused.* It was "stimulus discontinuity": the tone jumped in phase mid-window (M10 step > 20°), retryable once. A jump now goes undetected (§16 caveat 6) | — | — |
| E901 | any | Unexpected software error (stack trace in the app log; device safe-stated) | no | Call engineering (D-036) |

**Warnings (W-codes, D-036):** they never change a verdict. They're shown to the operator and recorded in `metadata.context`.

| Code | Meaning | Operator action |
|---|---|---|
| W101 | Safe-state `baseline` failed on exit (after 1 retry) | Unplug the DUT before the next one |
| W201 | A result file couldn't be written after 3 retries (e.g. the CSV is open in Excel); the run was saved to a fallback CSV | Close the file; tell engineering |
| W301 | A network upload failed (future integration); the run stays queued | None; it retries in the background |

---

## 10. Operator view (D-003, D-053)

The full screen spec is `docs/gui_spec.md`. What it means for this plan:
- **The DUT is one sensor with 8 channels** (D-002 reversed). It passes only if all 8 channels pass; each channel still gets its own result and primary failure (§8).
- **Before:** the plan name (PROD, locked), plan and limit versions, the fixture status (actuator and sensor connected), the **DUT serial field** and **Start**.
  - **Every run needs a DUT serial (D-047):** Start stays disabled until a valid serial is entered (settings `serial_pattern`).
  - **Scanner-friendly:** Enter in the serial field starts the run.
  - The field is locked during the run and the result, and cleared on Next for the next DUT.
  - The **test-step table** already lists every step of the plan with its limits, as a preview.
- **During:** the sequence bar (PROD: Connect → Baseline → Tone 10 Hz → End) with the current step lit and its time left; the **actuator indicator** (ON with the command, frequency and reply, or OFF); the table fills in as each step is judged; 8 live strips.
- **After:**
  - **Banner:** **PASS**, **FAIL (n of 8 channels)** naming the failed channels (not their reasons, D-055), or **ERROR Exxx** with the one-line action from §9. Each failed channel's primary-failure message (§8) is in the table and the channel's tooltip.
  - **Test-step table:** for every step, its operator name, Low, High, the 8 measurements (✓/✗), the step result and any error code; a Channel result row.
- **Operator mode shows measurements and limits (D-053 revises D-003), but never step IDs or diagnostics.** Its sequence is always PROD (D-040).
- **Test mode** (the engineering view, behind it): the same screen plus the PROD/ENG picker, step IDs, the run header, raw windows and the run's **diagnostics, read-only** (D-029). Diagnostics are never editable and never affect a verdict.

---

## 11. Determinism rules (D-025, D-027)

1. Fits and spectra use **fs_nom = 150 Hz**; the measured line rate is validity only.
2. Windows are counted in lines, never in clock time.
3. Every window is a **multiple of 30 lines**, so it holds whole cycles at 5, 10 and 15 Hz. Any new frequency or window length must keep this property.
4. A fixed evaluation order. No step depends on another, and no channel's result depends on another channel's (D-056).
5. Limits are inclusive and compared to unrounded values. Diagnostics are stored unrounded.
6. Version pinning: sequence, plan, limit set, metric dictionary, app and numeric library versions in each run, with raw windows stored, so any result or diagnostic can be recomputed.
7. The only timing-dependent element is the 0.5 s reply timeout.
8. No coupling between channels or between steps since D-056. (Before, phase depended on the other sensors through the median reference.)

---

## 12. Data record (D-029, D-032, D-048)

**One self-contained CSV per run (D-048), in a folder per day:**

```
output/<YYYY-MM-DD>/<SERIAL>_<run_id>.csv        one row per test step × sensor
output/<YYYY-MM-DD>/<SERIAL>_<run_id>_raw.csv    every capture window, all 8 sensors
output/logs/app.log                              application log (NFR-09)
```

- **Serial and run ID together name the file.** A serial isn't unique per run: retries, ENG audits and retests after rework all reuse it. The run ID is the one key that is unique for every test attempt; it also links a retry to its original (`retest_of`) and the CSV to its raw file.
- **Every run writes a file:** PASS, FAIL, ERROR and ABORTED. The file is written to a temp name and then renamed, so a reader never sees half a run.

**Columns** (`tp001-csv-v2`). Every row repeats the run fields, so any number of run files concatenate into one production table with no joins:

| Group | Columns |
|---|---|
| Run (on every row) | `test_plan` (e.g. TP-001), `run_id`, `serial` (operator-entered, D-047), `timestamp`, `station`, `sequence` (PROD/ENG), `plan_version`, `limit_set`, `app_version`, `device_usb_serial` (automatic), `outcome` (PASS / FAIL / ERROR / ABORTED), `failed_sensors`, `error_code`, `attempt`, `retest_of` |
| Step | `step_id`, `step_name`, `sensor` (`ch1`…`ch8` or `RUN`), `capture`, `value` (unrounded), `unit`, `low`, `high`, `result` (PASS / FAIL / NOT TESTED), `not_tested_reason`, `fault_family`, `primary_failure` |
| Run-level rows only | `metadata`: the single-line JSON (D-029), filled on the two `RUN` rows (1000 CONNECT and 1900 RUN_INTEGRITY), empty on sensor rows |

- **Rows per run:** 2 + 5 × 8 = 42 (PROD), 2 + 17 × 8 = 138 (ENG).
- **Both run-level rows are always present.** A connection fault (E1xx) FAILs 1000; any other fault FAILs 1900; an abort leaves the missing one NOT TESTED.

**`metadata` JSON rules:**
- **Versioned:** `"schema"` (e.g. `"tp001-meta-v2"`; v2 dropped `stimulus_step_deg`, D-058) for the JSON layout, `"format"` (`"tp001-csv-v2"`) for the CSV layout. The CSV's old `schema` column became `test_plan` (D-048).
- **Fixed shape:** `diagnostics` → capture → metric key → sensor → value. PROD and ENG use the same shape.
- **Keys are metric keys** (`BL_STUCK_RUN`, `T10_SPUR`): a promoted diagnostic keeps its history.
- **Unrounded numbers;** `null` when not computed.
- **`raw`** names the matching `_raw.csv` and its SHA-256.

```json
{
  "schema": "tp001-meta-v2",
  "format": "tp001-csv-v2",
  "context": {
    "fs_nom_hz": 150.0,
    "line_rate_hz": {"BL": 150.012, "T10": 150.013},
    "commands": [{"t": 0.002, "cmd": "baseline"}, {"t": 2.21, "cmd": "response:10"}, {"t": 4.42, "cmd": "baseline"}],
    "duration_s": {"BL": 2.2, "T10": 2.2, "total": 4.98},
    "error_detail": "", "warnings": []
  },
  "diagnostics": {"BL": {"BL_STUCK_RUN": {"ch1": 2}}, "T10": {"T10_SNR": {"ch1": -12.4}}},
  "raw": {"path": "SN-0001_R20261107-231441-bce2_raw.csv", "sha256": "…"}
}
```
(Trimmed: each metric holds all 8 sensors.)

**Analysis access:**
- **Any CSV tool** opens a run file directly. For production data, concatenate the day's (or year's) run files.
- The `metadata` column is parsed with any JSON parser.
- **Industry fit:** this maps onto STDF (each sensor is a site, each limit step a parametric test record, the validity steps functional tests). PROD and ENG share step IDs and metric keys.

---

## 13. Station-level safeguards (D-020, D-021: documented, not implemented in this assignment)
- **Golden-unit check at shift start:** a known-good unit must PASS before production testing.
- **Consecutive-fail lockout:** 3 runs in a row with all 8 sensors failing → the station stops: "Check station before continuing".

Data behind them (DOE-1, 2 s windows): no stimulus gives ≤ 8 a.u. at f (ch4 ≤ 52); stimulus on gives ≥ 797 on healthy sensors.

---

## 14. Not covered (for the report)
| Not covered | Why | How it would be covered |
|---|---|---|
| Linearity vs drive level | No amplitude command | Multi-level stimulus on real hardware |
| Crosstalk, swapped channels | One actuator drives all 8 | Per-position stimulus on the fixture |
| State after a power cycle | Not tested (D-010) | A possible DOE-2 |
| Unit-to-unit spread | ~5 healthy sensors (D-019) | Re-derive limits from production data |
| Reproducibility (stations, days, temperature) | One station, 101 min | Golden unit with SPC, Gage R&R |
| Actuator output verification | Ack-based (D-020) | A fixture reference sensor (D-016) |
| Actuator that doesn't stop, as a station ERROR | E207 dropped (D-057). In ENG it shows as every tone channel failing 5050 (a DUT FAIL, "Did not recover"); in PROD there is no REC capture, only the END reply check (E204) | Build E207: in REC, most of the channels that carried the last tone still show it → ERROR |
| The actuator's tone jumping mid-window (the Teensy's counter wrap, ~every 71.6 min) | Assumed never to happen (D-058); E211 removed | A jump reads as low amplitude: a healthy channel can falsely FAIL "Weak response" (~1 PROD run in 2,500 here). Restore M10/E211 (D-052) |
| A data line lost inside a tone window | E211 caught it as a phase step (1 line at 10/15 Hz, 2 at 5 Hz); the line-rate check needs > 1% (3+ lines in 2 s) | Restore M10, or count lines against host time per block. Never seen: the line rate read 150.01 Hz in every capture |
| **In PROD only:** frequency-dependent and recovery faults; stuck and glitch as limits | Pruned (D-028) | ENG audits, PROD diagnostics, promotion triggers (§15) |
| Reversed polarity, channel delay, lost bandwidth | All channels assumed in phase for this assignment (D-056) | Restore a phase step (M5 vs the other channels, or vs a fixture reference sensor) |

---

## 15. Revisit and promotion triggers

**Promotion triggers (a PROD diagnostic or ENG-only item becomes a PROD limit step):**

| Item | Watched by | Promote when |
|---|---|---|
| `BL_STUCK_RUN` (diagnostic) | SPC on PROD `metadata` | Any sensor shows stuck runs beyond the ENG limit (2060) |
| `BL_OUTLIERS` (diagnostic) | SPC on PROD `metadata` | Any sensor shows outliers. **Before promoting:** re-derive the threshold for the real noise distribution (M8) |
| Second and third tones (T5, T15) | ENG audits | An audit sensor differs between tones, or fails at T5/T15 but passes at T10 |
| Recovery capture (REC) | ENG audits | Any audit sensor fails 5050 or 5060 |
| Residual / spur / half-window (diagnostics) | SPC, ENG audits | The matching fault appears (D-017, D-018) |
| A new failure mode in returns or field data | — | Add a step to ENG, then apply the §3 rule |

**When promoted,** a diagnostic becomes a limit step with a new step ID and **the same metric key**, so its history in `metadata` carries straight on.

**Other revisit triggers:**

| Item | Revisit when | Ref |
|---|---|---|
| Offset limit (2040) | Full-scale range known → headroom limit | D-013 |
| Amplitude limits (6040) | A product spec gives nominal sensitivity and tolerance | D-014 |
| All channels in phase: no phase step (D-056) | A polarity, delay or bandwidth fault is seen; or the real sensor's channels can differ in phase; or the fixture gains a reference sensor | D-056 (reverses D-016) |
| No phase jumps: no stimulus check (D-058) | A jump is seen on a station (the Teensy's counter wrap is known), a run shows healthy channels reading ~17% low together, or lines are lost | D-058 (reverses D-052) |
| All limit values | More units are available | D-019 |
| Ack = correct stimulus | Real hardware | D-020 |
| Station safeguards | Beyond take-home scope | D-021 |
| Grouped validity steps | An MES requires per-check records | D-023 |
| Fixed time base | A different device clock | D-025 |
| ENG audit sampling rate | Production starts (TBD) | D-028 |
| Diagnostics in JSON | Trending diagnostics across many runs becomes routine → a dedicated table or view | D-029 |
| Protocol rules | Firmware changes | D-007–D-010 |

---

## 16. Limit set L-v2 (D-056: L-v1's values without phase; L-v1 frozen with L-v0's values)

**L-v2 (2026-11-08, D-056), what the station runs:** `config/TP-001_limits_v2.yaml`, generated by the same tool into `benchtop_testing/08_limits_v2_derivation/`. It is L-v1 without the three phase steps. Amplitude is now fitted at the response frequency (M3); the healthy population is unchanged (ch2, 3, 5, 6, 7), but at **1 Hz** (2 cycles per 2 s window) the measured frequency is less exact and healthy amplitudes read +1.9 a.u. on average, which moves the healthy range by ~0.1 a.u.: **6040 high 1128.3 → 1128.5**; every other value is unchanged. At the plan's tones (5, 10, 15 Hz) healthy amplitudes move ≤ 0.2 a.u. (≤ 1.9 for noisy ch4). Same PROD verdicts and primary failures on all 190 runs. The golden tests that pinned these differences on DOE-1 were removed with the bench data (D-060); re-running `derive_limits.py` on the bench reproduces L-v2.

**L-v1 (kept as the record) file:** `config/archive/TP-001_limits_v1.yaml` (YAML, D-045; generated, so edit the derivation, not the file), generated by `benchtop_testing/tools/derive_limits.py`, which calls the product's `sensortest.characterization` (D-050). Report: `benchtop_testing/06_limits_v1_derivation/limits_report.md`. It is **L-v0 with a new label**: every step, validity entry and constant is identical (`tests/test_limits_v1.py`). L-v0 (`config/archive/TP-001_limits_v0.yaml`, derived by `benchtop_testing/tools/derive_limits_v0.py`, report `benchtop_testing/04_limits_v0_derivation/limits_v0_report.md`) is kept as the golden reference.

**Status:** **frozen 2026-11-08 (D-051).** The path there:
1. **L-v0** (2026-11-07): provisional, "exploration math".
2. **Recomputed with the station's own code: identical** (every metric value, every limit, and the PROD verdicts over all 190 runs; D-050, `tests/test_golden_lv0.py`). Closes D-019 risk 1. *(Those golden tests were removed in D-060, when bench data left the repository.)*
3. **Gauge run on the device** (D-051, `benchtop_testing/05_gauge_run_l_v1/report.md`): 20 PROD + 20 ENG through the station's own runner and drivers.
   - Zero ERRORs and zero false fails: every run gave the expected verdict and primary failure on all 8 sensors. The tone → tone switches in ENG were clean, which closes D-019 risk 3.
   - The added drift check (every step's mean within 3 DOE-1 SDs of DOE-1) **failed as run**: ENG T10 amplitude about 0.9 % low on the healthy sensors. The whole shift came from **one run**, in which the actuator's tone jumped in phase mid-window (caveat 6). **Without that run it passes** (largest shift 1.47 SD; T10 amplitude within 0.4 a.u. of DOE-1). The run is excluded from the drift check only, with its reason in the report.

**How the data was re-cut:** every DOE-1 run was cut to this plan's exact timing.
- After each command: discard 30 lines, then take 300 lines (REC: 150).
- That gives 200 BL windows, 190 tone windows and 190 REC windows per sensor.
- DOE-1 runs are baseline → tone → baseline, the same order as PROD.
- The response is flat over 1–19 Hz, so each tone run stands in for T10, measured at its own commanded f.
- The re-cut repeatability matches the noise + jitter model (e.g. ch7 offset SD 0.48 measured vs 0.50 predicted), which closes D-019 risk 2.

**Limits are common to all 8 sensors** (one product spec, D-002).

| Step | Low | High | Unit | How it was set |
|---|---|---|---|---|
| **2040 BL_OFFSET** | **−127.1** | **455.8** | a.u. | D-019: healthy range −50.0 … 378.8 (all but ch5) ± 10% of the span, ± 3 × 11.4 (largest 2 s repeatability SD, ch4) |
| **2050 BL_NOISE_STD** | **5.6** | **33.9** | a.u. | D-019: healthy range 8.66 … 28.77 (all but ch4) × 0.9 / × 1.1, ± 3 × 0.73 (ch1) |
| **6040 T10_AMPLITUDE** (and 3040, 4040) | **712.8** | **1128.5** | a.u. | D-019: healthy range 799.7 … 1019.7 (ch2, ch3, ch5, ch6, ch7) × 0.9 / × 1.1, ± 3 × 2.28. M3 at the response frequency (D-056); L-v1 had 1128.3 at the commanded f |
| **6050 T10_FREQUENCY** (3050: 4.5–5.5; 4050: 14.5–15.5) | **9.5** | **10.5** | Hz | Design: commanded f ± 0.5 Hz (one native FFT bin at 2 s). Healthy error ≤ 0.017 Hz; ch8 is 10 Hz off. **Gated:** judged only if the dominant peak ≥ 100 a.u. (D-030) |
| 2060 / 5080 STUCK_RUN (ENG) | — | 5 | samples | Design: P(run ≥ 5) ≈ 3 × 10⁻¹⁰ per window with 1,000 noise levels. Worst seen: 3 |
| 2070 / 5090 OUTLIERS (ENG) | — | 0 | count | Design (D-011). Valid for uniform noise only (M8) |
| 5050 REC_TONE_ABSENT (ENG) | — | 100 | a.u. | Tone-present threshold. Worst seen: 79 on ch4 (1 s window, noise) |
| 5060 REC_OFFSET_SHIFT (ENG) | −60.5 | 61.7 | a.u. | D-019 (1 s window). **Loose:** ch4's noise sets the ±3 SD guard band (SD 20) |
| Validity: CONNECT line rate / idle | 148.5 | 151.5 / < 100 | Hz / a.u. | D-008 / D-030 |

**Verification on 190 simulated PROD runs:**

| Sensor | PASS | FAIL | Operator sees | Tightest margin* |
|---|---|---|---|---|
| ch2, ch3, ch6, ch7 | **190** | 0 | — | noise, ≥ 12.3 SD inside |
| ch1 | 0 | **190** | Weak response (190) | amplitude 287 SD below the minimum; noise 7.1 SD inside |
| ch4 | 0 | **190** | Noisy (190) | noise 33 SD above; amplitude 4.8 SD above the maximum (fails high in all 190 runs) |
| ch5 | 0 | **190** | Offset out of range (190) | offset ≈ 1,100 SD above |
| ch8 | 0 | **190** | Wrong frequency (190) | frequency 10 Hz off (gated: peak ≈ 890 a.u.); amplitude ≈ 2.6 a.u. at f |

\*Distance from the sensor's mean to its nearest limit, in its own run-to-run SDs. Positive = inside.
- The tightest healthy margin in PROD is ch1's noise at 7.1 SD (it fails anyway, on amplitude), then ch6's noise at 12.3 SD.
- No healthy sensor came close to any limit.

**Caveats:**
1. **Partly circular** (D-019): each population leaves out the sensor already judged faulty on that metric. The verdicts confirm consistency, not independent truth.
2. **Population of ~5 healthy sensors.** The real unit-to-unit spread is unknown. Re-derive from production data (D-004).
3. **Common limits for all sensors** assume one sensor type with one spec. A noisy sensor in a population (ch4 for offset) widens everyone's guard band. That's acceptable for offset, but it makes **5060 REC_OFFSET_SHIFT** loose (±61). A per-sensor normalised version (shift ÷ its own noise SE, as in DOE-1 H4) would be tighter. ENG only; **kept as is in L-v1** and noted (D-051).
4. **ch4's amplitude** fails high by 4.8 SD (min 1,144 vs max limit 1,128). It's reliable here, and ch4 fails noise regardless.
5. **Outliers (M8)** and **stuck runs (M7)** depend on this simulator's uniform, finely quantized noise. Re-derive both for real hardware.
6. **The actuator's tone jumps in phase about every 71.6 minutes** (found in the gauge run, D-051). Most likely its 32-bit microsecond counter wrapping: a 10 Hz tone jumps +118° in one sample. A tone window that contains the jump reads a lower amplitude (one sine fitted across two phases), so a healthy sensor could falsely FAIL 6040 "Weak response": about 1 PROD run in 2,500 (Claude's estimate). It is a station fault, not a DUT fault, and a real counter-example to D-020. **Handled by D-052** with the stimulus check E211 (a retryable ERROR instead of a FAIL), then **assumed away by D-058:** for this assignment the tone is assumed never to jump, so E211 is gone and a jump inside a tone window again reads as low amplitude and can falsely FAIL a healthy channel (~1 PROD run in 2,500 on this Teensy). The finding stays true of the device (`docs/device_protocol.md`).
7. **All channels are assumed in phase (D-056).** There is no phase step, so a channel with reversed polarity, a delay or reduced bandwidth passes: on a recorded T10 window, a reversed ch2 and a ch2 delayed by one line (6.7 ms, −24°) both read amplitude 1002 and frequency 9.998. Phase never failed a unit in DOE-1 (healthy |Δφ| ≤ 0.42° over 190 runs), so no verdict here changes; the gap matters for real units (swapped leads, a bad filter part). Revisit per §15.

---

## 17. Change log
| Version | Date | Change |
|---|---|---|
| v0 draft (1st) | 2026-11-07 | First complete procedure: capture-based steps T1–T4 |
| v0 draft (2nd) | 2026-11-07 | One measured thing per test step (D-022) |
| v0 draft (3rd) | 2026-11-07 | Grouped validity steps with error codes; session capture merged into baseline; fixed 150 Hz time base; priorities; determinism; operator view (D-023–D-027) |
| v0 draft (4th) | 2026-11-07 | Split into TP-001-PROD and TP-001-ENG (D-028): PROD has 5 limits, one 10 Hz tone, no recovery, ≈ 5.0 s; ENG is the full plan, ≈ 10.5 s. Coverage matrix; promotion triggers |
| v0 draft (5th) | 2026-11-07 | **Log-only metrics become diagnostics (D-029):** they're no longer test steps, never shown on the operator UI, and are saved in a versioned `metadata` JSON (context, diagnostics, raw reference). Filter fields stay as normal columns; units live in a metric dictionary. Diagnostic IDs are retired; metric keys are kept for promotion continuity. Result rows: 42 per PROD run |
| v0 draft (6th) | 2026-11-07 | **Limit set L-v0** (§16) from DOE-1 re-cut to the plan's timing. Verified on 190 simulated PROD runs: exact expected verdicts and operator messages. **Frequency gate (D-030):** 6050 is judged only when a tone is present (peak ≥ 100 a.u.); otherwise ch1's noise peak would read as "Wrong frequency". One tone-present threshold (100 a.u.) for idle, actuator stopped, REC tone absent and the frequency gate |
| v0 draft (7th) | 2026-11-07 | **DUT serial per run (D-047)** and **one self-contained CSV per run (D-048)**: `output/<date>/<SERIAL>_<run_id>.csv` + `_raw.csv`, run fields on every row, metadata on the RUN rows, `schema` column → `test_plan`, both run-level rows always present, atomic write. `data_out/` → `output/` |
| **v1** | 2026-11-08 | **Limit set L-v1 frozen** (§16, D-051): L-v0's values, after the station's code reproduced L-v0 exactly (D-050) and a 20 + 20-run gauge run on the device (zero ERRORs, zero false fails; drift check failed as run on one run hit by an actuator phase jump, passed with it excluded). New caveat 6: the actuator's counter-wrap phase jump. 5060 kept loose and noted |
| v1.1 | 2026-11-08 | **Stimulus check (D-052):** new method M10 (stimulus step) and 1900 check E211 "Stimulus discontinuity" on every tone capture, before its steps are judged; retryable once by the operator. The step is saved for every tone capture in `metadata.context.stimulus_step_deg`. Caveat 6 in §16 now handled |
| **v2.1** | 2026-11-08 | **D-058:** no phase jumps assumed: the stimulus check E211 and method M10 removed, with the fixture's stimulus-step settings, the replay fault `phase_jump` and `stimulus_step_deg` in the metadata (schema `tp001-meta-v2`). Sensor test file plan version v2.1. Not covered: a real jump (false FAIL), a line lost inside a tone window (§14) |
| v2 (2) | 2026-11-08 | **D-057:** E207 "actuator didn't stop" (ENG) dropped: it was never built. 5050 is now the only data check that the tone stopped. The reference set (used by phase and E207) is gone from the glossary |
| **v2** | 2026-11-08 | **D-056:** all channels assumed in phase: phase steps 3060/6060/4060, method M5 and the timing family removed; no step depends on another. Amplitude (6040/3040/4040) is fitted at the response frequency when a tone is present, so ch8 fails 6050 only. PROD 4 limit steps, ENG 14. Limit set **L-v2** (§16). Coverage gap noted (§3, §16 caveat 7) |
| v1.1 (3) | 2026-11-08 | **§10 banner (D-055):** a FAIL names the failed channels only; the primary-failure messages stay in the table and tooltips |
| v1.1 (2) | 2026-11-08 | **§10 operator view rewritten for the GUI spec (D-053):** one DUT with 8 channels (D-002 reversed); operator mode shows the test-step table with limits and measurements; test mode is the same screen plus extras. See `docs/gui_spec.md` |
