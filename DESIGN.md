# DESIGN.md: how I used AI tools

The brief asks where and how AI tools were used, and, given the time constraints, where AI-assisted work might be incorrect or need review. This is a high-level overview. The detailed record (a dated log of every step, and a decision log with the reasoning and rejected options for each design decision) is kept in my planning notes, outside this repository. Decision IDs such as D-056 in the code and documents refer to that log.

## The tool

**Claude (Anthropic), through Claude Code in the Claude desktop app.** It worked directly in this repository, on the connected Teensy, and in my planning notes. I used it as an implementation and analysis assistant: I wrote the requirements, plans and structure, and Claude filled in the implementations, ran the analyses I asked for, and wrote up documents from my drafts.

## How I worked with it

- **I authored the requirements, the plans and the documentation; Claude wrote them up.** I drafted:
  - the software's requirements in the PRD;
  - the test plan: its sequences, its steps and their rules;
  - the screen's requirements;
  - the content of the documentation.

  I fed these to Claude to expand, format and keep consistent.
- **I designed the software and stubbed it out; Claude filled in the implementations.** I set the architecture (ports and adapters, plugin registries, threads and queues, separate actuator and sensor drivers) and stubbed out its parts and interfaces. Claude implemented the modules behind them and wrote the tests.
- **I made the decisions.** For each one, Claude asked structured, multiple-choice questions with a recommended option and the alternatives. I picked an option, gave my own answer, or asked for more evidence first (for example, renders of three screen layouts before choosing one). Each decision was logged with its reasoning and the options not chosen, with my reasoning kept separate from "accepted Claude's recommendation".
- **Evidence before changes.** When I proposed a simplification, Claude first measured its effect on the recorded data or the device, then changed the code. For example, it injected faults into a recorded window before I removed the phase check, and hid the bench data to count which tests depend on it.
- **Safety rules for the hardware**, which I set up front and which are enforced in code: never overwrite the Teensy's firmware, never open the port at 134 baud (it starts the bootloader), and send only `baseline` and `response:N`.

## Where AI was used

| Phase | What I did | What Claude did |
|---|---|---|
| **Planning** | Wrote the roadmap: the time box, the guiding questions, the two-part structure (understand the device first, then build the software), the priorities and the order of work | Listed the brief's ambiguities and the stack trade-offs; turned my roadmap into section budgets |
| **Hardware bring-up** | Planned the bring-up, set the safety rules, and confirmed the protocol facts one by one | Wrote and ran the capture scripts on the Teensy, and wrote up the protocol reference from the captures. The captures showed the device runs at 150 Hz (the brief says 100) and accepts 1–19 Hz (the brief says 1–25) |
| **Characterization** | Designed the experiments (which checks to make, the repeatability DOE), reviewed every chart, and settled the line-test decisions | Wrote and ran the sweep and DOE scripts (200 runs), built the analysis and its charts, and fitted the signal model (offset + uniform noise + one shared tone) |
| **Test plan and limits** | Drafted the test plan: the production and engineering sequences and the method for cutting one from the other, one measured quantity per step, the outcomes and the error codes. Set the rule for deriving limits | Wrote the plan up in full, and derived the limit values from the DOE data with my rule. The derivation turned up a frequency check that failed a channel for the wrong reason; I approved the fix (a tone-present gate) |
| **Software** | Wrote the PRD's requirements (modular, extensible, reliable, a core separated from tests, devices and integrations); designed the architecture and the state machine's behaviour; made the structural calls (CSV records, YAML files, threads and queues, one driver per role); stubbed out the packages and interfaces | Wrote the PRD up from my requirements; implemented the modules behind my interfaces (engine, drivers, records, headless runner); wrote the tests |
| **On the device** | Asked for a gauge run before freezing the limits and chose its pass rules | Ran 40 runs on the Teensy, and traced one odd run to the actuator's tone jumping in phase about every 71.6 minutes (most likely a counter wrapping in the firmware) |
| **The station screen** | Wrote the screen's requirements (operator and test modes, what the operator sees for all 8 channels); chose layout A from three renders; approved the PyQt render; asked for changes after seeing it | Rendered the layout options, then built the screen in PyQt6 to my spec. It found that antialiased drawing biased the measured line rate by up to −0.27%, and turned antialiasing off |
| **Simplifications** | Chose to assume all channels are in phase and that the tone never jumps; chose to measure amplitude at the frequency each channel responds at; dropped an unbuilt check | Assessed each idea on recorded data first, then changed the code and documented the gaps each assumption leaves |
| **Repository and documents** | Set the repository layout and the data policy (no bench or manufacturing data in git); chose synthetic test data; wrote the report | Reorganized the repository to my layout, built synthetic recordings from the device model so every test runs without hardware or data, and wrote up documents from my drafts. Its edits to report sections are marked for my review |

## Where AI-assisted work may be incorrect or need review

1. **Device behaviour inferred, not read from source.** The firmware's command parsing, the counter-wrap explanation for the phase jump, and the noise and timing-jitter model all come from observed data, not from the firmware itself.
2. **Estimates.** The false-fail rate the phase jump would cause (about 1 production run in 2,500), the time budgets, and the USB overhead are Claude's estimates, not measurements.
3. **The limits.** They come from about five healthy channels on one device, and partly from knowing which channels are faulty, so they are only as good as that sample. Re-derive them from production units. The amplitude high limit moved from 1128.3 to 1128.5 a.u. when amplitude started being measured at the response frequency.
4. **Implementation details Claude chose without asking.** These are listed as "Claude's choices, to review" in the decision log. Examples:
   - some details of the station screen;
   - which error code covers a device that sends nothing;
   - the device model used for the synthetic test data;
   - making that synthetic data the default fixture.
5. **Assumptions I chose knowingly.** "All channels are in phase" and "the tone never jumps" simplify the station, but they leave gaps: a reversed or delayed channel would pass, and a real phase jump (the Teensy does have them) would look like a weak response. Both are listed in TP-001 §14.
6. **Test coverage.**
   - The tests run on synthetic recordings of the measured device model, not on real recordings.
   - The tests that compared the station's maths with the original DOE limits were removed with the bench data.
   - The screen was tested through its own buttons by code, not yet by a person, and the keyboard shortcuts weren't tried by hand.
   - Linux and macOS are tested and built in CI, and each built app passes a headless smoke test; their GUIs haven't been opened by a person yet.
7. **Written material.** Claude wrote up documents from my drafts and notes, and edited some report sections in my voice. I review all of that text before it's submitted.

**Mistakes that the checks caught along the way** (examples):
- a test with the wrong column count;
- a misleading warning from the serial driver;
- a screen layout that pushed the window to 1,685 px wide;
- a Qt visibility bug that only showed up with the window hidden;
- a test tolerance too tight for the noisy channel;
- the plot drawing that skewed the line-rate measurement.

## How the AI's work was checked

- **On the device:** `pytest --hardware` runs the production and engineering plans on the Teensy, the 40-run gauge run checked the limits, and screenshots of the real app on the Teensy confirm the screen.
- **Without hardware:** 94 tests, run on synthetic recordings, need no hardware and no bench data. They include the full run, every injected fault ending in the right error with the actuator turned off, and the screen driven through its own buttons. Lint is clean.
- **Before and after each change:** measurements on recorded data or the device, written down with the change.
