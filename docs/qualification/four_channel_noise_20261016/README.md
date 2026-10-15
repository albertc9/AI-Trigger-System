# Four-channel capture replay with per-channel calibration

At threshold 7.2, the first 39 consecutive 256-sample windows produce **2
qualifying scores**. At the user-confirmed 1.00 GS/s sample rate, their
9.984 microseconds of exposure give a short-record replay estimate of
**200,320.5 Hz**, approximately 200 kHz. This is not an on-board rate
measurement or a reliable long-duration rate estimate.

This capture is too short to determine a threshold giving approximately
1 Hz. The smallest hardware threshold rejecting every scanned window is
**10.6875**; zero events in this capture must not be interpreted as 1 Hz.

## Input and calibration

- AI-Trigger-System source commit: `7f0dc86`, branch `v4.1`.
- Corresponding delivered gateware: `950ff61`, branch `oct_15_gain_offset`;
  production build input `efb00fd`.
- Input: `/Users/albert/Downloads/all_channels_ampl (1).csv`.
- SHA-256: `51df65125651790cadfcf5c49450fba7b7aca3064f696cad5093ba4da2bec428`.
- 10,000 rows, four simultaneous channels, physical mV, no timestamp column.
- User confirmed 1.00 GS/s and continuous CNN mode. Rows are assumed to be
  consecutive samples with no gaps; total capture exposure is 10 microseconds.
- Recover original signed ADC codes from the CSV's 800 mV / 4094-code lattice.
  Maximum reconstruction residual is 0.000000656 mV. Using 4096 instead
  recovers exactly the same integer codes for all 40,000 values in this file.
  FPGA calibration then uses its specified 800 mV / 4096-code scale.

| Channel | Gain | Baseline subtracted, mV | Measured mean, mV | AC RMS, mV |
| --- | ---: | ---: | ---: | ---: |
| 0 | 4.421578 | -0.468 | -0.467611 | 2.827045 |
| 1 | 4.475522 | -0.014 | -0.013874 | 2.792970 |
| 2 | 4.417241 | +1.118 | +1.118437 | 2.829821 |
| 3 | 4.400045 | +0.585 | +0.585403 | 2.840880 |

Use the committed fixed calibration unchanged, with no per-window mean or
RMS normalization. Pre-quantization CNN input AC RMS is approximately
0.999512 model units in every channel. Capture means/RMS match the data
used to select the calibration; this replay is not an independent test of
calibration stability across recordings or FPGA reloads.

## Observations

The continuous input consumes 256 samples per window. Starting at row zero,
39 complete non-overlapping windows use 9,984 samples; the final 16 samples
cannot form a complete window and are excluded. Native signed score uses
units of 1/1024. Qualification uses strict `score > threshold`.

| Requested/effective threshold | External word | Qualifying windows / 39 | Short-record replay rate |
| --- | ---: | ---: | ---: |
| 7.2, mathematical comparison | not exactly representable | 2 | 200,320.5 Hz |
| 7.1875 | 115 / `0x73` | 2 | 200,320.5 Hz |
| 7.25 | 116 / `0x74` | 2 | 200,320.5 Hz |
| 8.5 | 136 / `0x88` | 0 | zero observed in this alignment |
| 10.6875 | 171 / `0xAB` | 0 | zero observed in every scanned alignment |

The two phase-zero hits start at sample 3328 (score 7.326171875) and sample
9216 (score 8.4716796875). Phase-zero scores range from 1.09765625 to
8.4716796875. External threshold granularity is 1/16, so 7.2 cannot be
configured exactly; the nearest word is 115, giving 7.1875.

To check alignment sensitivity, also evaluate all 9,745 valid 256-sample
windows at stride one, without padding or wrapping. All scores are recorded
in [scores.csv](scores.csv). At 7.2, 512 sliding positions qualify; at
7.1875, 512 qualify; at 7.25, 495 qualify. These overlapping positions are
correlated and must not be counted as separate exposure or independent
trigger events. Across the 256 possible sample phases, non-overlapping
streams contain 0 to 5 hits in 38 or 39 windows. Hardware beat-aligned
starts are also included in this diagnostic scan.

The maximum score across all starts is **10.669921875**, at sample 4508,
which is also a valid four-sample beat boundary. Thus word 171 / 10.6875 is
the smallest configurable threshold rejecting every scanned position. Word
170 / 10.625 leaves one sliding position above threshold. The transition
from one sliding exceedance to zero does not locate a 1 Hz operating point.

See [threshold_scan.csv](threshold_scan.csv), [summary.json](summary.json),
and [score_trace.png](score_trace.png) for the recorded scan and scores.

## Validation

- Rechecked all 66 resolved entries in the qualified source manifest and all
  93 HLS firmware-file hashes against current local files.
- Verified all 29 cached native CNN RTL/ROM files and the Verilator
  testbench against those sources. Recorded both native executable hashes.
- Actual fixed-point HLS C++ scores and actual Verilator CNN RTL agree
  exactly for **all 9,745 windows** in nine batches.
- Audited every native input slot against the four-channel ADC conversion
  table; all 9,978,880 calibrated samples match, with original time/channel
  order retained.
- Full Trigger mixed-language Vivado 2022.2 simulation, with real XPM and
  real CNN RTL, completes all 39 full-rate windows at ADC 250 MHz (four
  samples/beat = 1 GS/s) and CNN 195 MHz. Words 115 and 116 yield two events;
  word 171 yields zero. Score, event framing, metadata, no-loss assertions,
  and original raw event payload assertions pass. Channels 4..7 are zero
  testbench filler because the supplied capture has only channels 0..3.
- All 66 entries in the reference source manifest also match the server
  simulation closure. Only the isolated testbench CNN oscillator changes
  from its default 200 MHz to 195 MHz; production RTL is unchanged.

System simulation summary: [system_summary.json](system_summary.json).
Local C++/Verilator logs: `native_logs.tar.gz`. Full server simulation logs,
testbench, launchers and source manifest: `system_evidence.tar.gz`.
`SHA256SUMS` covers the retained analysis artifacts.

No production calibration, RTL, gateware, software or bitstream was changed
for this analysis. No push or board programming was performed.

## Why this cannot determine 1 Hz

At 1 GS/s, a loss-free stream offers 3,906,250 windows per second. A 1 Hz
rate therefore corresponds to an exceedance probability of approximately
**2.56e-7 per window**. This record provides only 39 full non-overlapping
windows. Its count-to-rate resolution is 100,160.3 Hz per observed event.
Sliding the same record or replaying it repeatedly adds no new time exposure.

For illustration only, under a stationary Poisson event assumption, zero
events over duration T have a one-sided 95% upper rate of `-ln(0.05)/T`.
With T = 9.984 microseconds, this is approximately **300,053 Hz**, even at
a threshold with no observed events. Rare-event extrapolation from a fitted
score tail is not justified by this capture.

To locate approximately 1 Hz, use the calibrated bitstream on the board and
count actual triggers over tens to hundreds of seconds per threshold,
tracking live time and any busy/loss conditions. Word 171 / 10.6875 is a
capture-based starting candidate, not a validated 1 Hz setting. Adjust the
existing threshold register in 1/16 steps using those counts; a threshold
scan does not require rebuilding the bitstream or recording every raw
sample for the entire measurement.

## Reproduce

The [replay.py](replay.py) analysis driver uses the qualified HLS native
reference executable and source manifest from
`build/channel_gain_offset_20261015/`, whose build procedure is recorded in
the preceding calibration qualification. It rechecks that closure and the
supplied Verilator cache before executing the new capture. For example:

```sh
/opt/homebrew/bin/python3 docs/qualification/four_channel_noise_20261016/replay.py \
  --root . \
  --csv '/Users/albert/Downloads/all_channels_ampl (1).csv' \
  --output build/noise_rate_20261016/reproduction \
  --verilator-cache /private/tmp/ai-native-score-v4-u9_nkobs \
  --sample-rate 1000000000
```

The output directory must not exist. `run_system.py` in the server archive
records the separate full-Trigger simulation, using an isolated copy of
the qualified server source snapshot. It does not edit either working
repository. Generated packed inputs and larger scratch data remain under
`build/noise_rate_20261016/`.
