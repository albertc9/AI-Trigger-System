# Windowed event rate

Starting point: AI-Trigger-System v4.1 RTL at `15e665a` (Bender dependency URL
update only). Final source: `ef510cf` on `v4.2`. Commits use the existing Albert
identity. No gateware integration or board test is part of this qualification.

`AI_TRIGGER_TOP` publishes a coarse event rate for the DAQ and host:

| Output | Meaning |
| --- | --- |
| `EVENT_RATE_COUNT[15:0]` | Emitted events in the window |
| `EVENT_RATE_WINDOW_SEC[6:0]` | Seconds covered by the window, `0..64` |
| `EVENT_RATE_HIGH` | A second in the window reached 1023 events |

An event is counted when its final beat transfers. One second is 3,906,250
accepted chunks of ADC data, exact at 1 GSa/s. The window is the last 64
completed seconds; all outputs update once per completed second. Each second
saturates at 1023 events, so the count cannot overflow. Reset and every change
of the active trigger mode clear the window. The new `EVENT_RATE_MONITOR` is
`CLK_ADC` only and adds no clock-domain crossing. The generic
`EVENT_RATE_BIN_CHUNKS` shortens a second for simulation; its default keeps the
gateware instantiation unchanged.

Local qualification: 42/42 GHDL benches pass, including the new unit bench
(per-bin counts, 64-bin sliding window, saturation and over-range flag, clear
on mode start and reset, handshake and bin-boundary rules) and the multimode
integration bench (published rate equals independently observed Capture-All
events; a runtime mode switch clears the rate). Three fault injections were
caught: ignoring `EVENT_READY`, moving a bin-closing event into the next bin,
and disconnecting the mode-start clear. The interface guard requires the new
ports and OOC output delays. Of 109 Python checks, the same two failures and
three errors reproduce on the untouched `15e665a` baseline (missing matplotlib
and a null-revision path dependency in `Bender.lock`); they are unrelated.

Server Vivado 2023.2 (Build 4029153) used dependency snapshots whose 38
resolved CNN core, wrapper and Hi-Lo files are byte-identical to the Mac and to
the v4.1 manifest; all 67 resolved sources matched the Mac before any run.
The server checkout only rewrote `Bender.lock` to its local dependency paths
(diff in `orchestration/`). The native reference rebuilt with Vitis HLS 2023.2
headers matches all six v4.1 data-file hashes.

All nine real-XPM system cases pass. `tb_native_system` now runs with 64-chunk
bins and checks every published rate against its own count of complete
events:

| Case | Events | Checked rate publications |
| --- | ---: | ---: |
| continuous | 324 | 17 |
| gaps (9 cycles, 1.3 ns phase) | 324 | 17 |
| threshold +32 | 143 | 17 |
| threshold -32 | 1056 | 17 |
| threshold minimum | 1096 | 17 |
| threshold maximum | 0 | 17 |
| overload, then reset and normal run | 2 + 324 | 34 |
| lane reset, five-mode switching | pass | not connected |

`tb_ai_trigger_top.sv` (dataset flow, not part of the nine cases) passes an
`xvlog` syntax check with its new rate observer.

Routed OOC, `xcku5p-ffvb676-2-e`, unchanged 250 MHz ADC / 200 MHz CNN clocks,
same tool for all three builds:

| Build | WNS (ns) | WHS (ns) | CLB LUTs | Registers | BRAM / URAM / DSP |
| --- | ---: | ---: | ---: | ---: | --- |
| v4.1 `15e665a` | +0.132 | +0.033 | 75,395 | 51,943 | 25 / 6 / 64 |
| v4.2 `e47ba83` | +0.273 | +0.035 | 75,483 | 52,010 | 25 / 6 / 64 |
| v4.2 `ef510cf` | +0.217 | +0.004 | 75,480 | 52,010 | 25 / 6 / 64 |

TNS and THS are zero in every build. The monitor hierarchy uses 75 LUTs (65
logic, 10 LUTRAM for the bin store) and 68 registers, with no block RAM, URAM
or DSP. `e47ba83` reported TIMING-18 on the 24 new output bits; `ef510cf` adds
their zero OOC output delay and the finding is gone. DRC and CDC summaries are
identical to v4.1. The `ef510cf` worst hold path is CNN lane metadata LUTRAM in
`CLK_CNN`, outside the monitor; the monitor's clock has +0.031 ns hold slack.
Timing differences between builds are placement variation, not new paths.

Not covered: gateware integration at 260/195 MHz, readout of the rate by the
DAQ or host, and board testing.

Files: `local_summary.json`, `local_logs.tar.gz`, and under `server/` the
source manifest, reference parity, nine-case summary, per-case simulation logs,
and the three OOC report sets with orchestration logs.
