#!/usr/bin/env python3
"""Plot four captured ADC channels with time-aligned Ch0/Ch2 AWG references."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import AutoMinorLocator, MultipleLocator
import numpy as np


ROOT = Path(__file__).resolve().parent
RECORDED = "#19ae80"
REFERENCE = "#f26835"
AWG_INPUT = "#9b9b9b"
DEFAULT_SIGNAL_DISPLAY_SCALE = 0.90


def filter_adc_codes(samples: np.ndarray, model: dict):
    """Reproduce the qualified HPF sample recurrence, including fixed-point rounding."""
    state_fraction = model["state_fractional_bits"]
    coefficient_fraction = model["product_fractional_bits"]
    state_limit = 1 << (model["state_width"] - 1)
    values = [int(sample) << state_fraction for sample in samples]
    for section in model["sections"]:
        result = []
        for n in range(len(values)):
            accumulator = sum(
                tap * values[n - k]
                for k, tap in enumerate(section["numerator_q16"])
                if n >= k
            )
            if n >= 8:
                accumulator -= section["a8_q16"] * result[n - 8]
            if n >= 16:
                accumulator -= section["a16_q16"] * result[n - 16]
            rounded = (accumulator + (1 << (coefficient_fraction - 1))) >> coefficient_fraction
            result.append(max(-state_limit, min(state_limit - 1, rounded)))
        values = result
    return np.array([
        max(-2048, min(2047, (value + (1 << (state_fraction - 1))) >> state_fraction))
        for value in values
    ], dtype=np.int64)


def load_data(data_dir: Path):
    manifest = json.loads((data_dir / "source_manifest.json").read_text())
    for name, item in manifest["data_files"].items():
        digest = hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"Input checksum mismatch: {name}")

    with (data_dir / "ai_event_0003.csv").open(newline="") as handle:
        timestamp = int(next(handle).strip().split("=", 1)[1])
        reader = csv.DictReader(handle)
        expected = ["sample"] + [f"ch{c}" for c in range(8)]
        if reader.fieldnames != expected:
            raise ValueError("Unexpected event CSV columns")
        rows = list(reader)
    sample = np.array([int(row["sample"]) for row in rows])
    if not np.array_equal(sample, np.arange(256)):
        raise ValueError("Expected 256 consecutive samples")
    adc = np.array([[int(row[f"ch{c}"]) for c in range(4)] for row in rows])
    if np.any(adc < -2048) or np.any(adc > 2047):
        raise ValueError("Capture is outside the signed 12-bit ADC range")

    with np.load(data_dir / "awg_reference.npz") as stored:
        reference = {key: stored[key].copy() for key in stored.files}
    if reference["post_hpf_adc"].shape != (1000, 2):
        raise ValueError("Expected two 1000-sample signal references")
    if not np.array_equal(reference["channels"], [0, 2]):
        raise ValueError("Reference channels must be Ch0 and Ch2")
    with (data_dir / "signal_delayed_2rms_noiseless_estimate.csv").open(newline="") as handle:
        pulse_reader = csv.DictReader(handle)
        if pulse_reader.fieldnames != ["Time", "CH1"]:
            raise ValueError("Unexpected noiseless estimate columns")
        pulse_rows = list(pulse_reader)
    pulse_time_ns = np.array([float(row["Time"]) * 1e9 for row in pulse_rows])
    pulse = np.array([float(row["CH1"]) for row in pulse_rows])
    if pulse.shape != (1000,) or not np.isfinite(pulse).all():
        raise ValueError("Expected a finite 1000-sample noiseless estimate")
    if not np.allclose(pulse_time_ns, reference["original_csv_time_ns"], rtol=0, atol=1e-9):
        raise ValueError("Noiseless estimate time axis does not match the AWG input")
    pulse *= manifest["source_template_constant_multiplier"]
    reference["noiseless_input_volts"] = np.column_stack(
        [pulse, np.roll(pulse, manifest["ch2_cyclic_delay_samples"])]
    )
    hpf_model = json.loads((data_dir / "hpf_lookahead.json").read_text())
    pulse_codes = np.rint(
        reference["noiseless_input_volts"] * 1000 / manifest["adc_mV_per_code"]
    ).astype(np.int64)
    if np.any(pulse_codes < -2048) or np.any(pulse_codes > 2047):
        raise ValueError("Noiseless pulse is outside the signed 12-bit ADC range")
    filtered_pulses = []
    for column in range(2):
        if not np.array_equal(
            filter_adc_codes(reference["raw_adc"][:, column], hpf_model),
            reference["post_hpf_adc"][:, column],
        ):
            raise ValueError("HPF model does not reproduce the frozen noisy reference")
        filtered_pulses.append(filter_adc_codes(pulse_codes[:, column], hpf_model))
    reference["noiseless_post_hpf_adc"] = np.column_stack(filtered_pulses)
    if timestamp != manifest["event_timestamp"]:
        raise ValueError("Event timestamp does not match provenance")
    return manifest, reference, adc


def align_time(reference: np.ndarray, capture: np.ndarray):
    """Locate one 256-sample capture in a periodic 1000-sample reference.

    Pearson correlation determines time alignment only. No amplitude scale or
    mean correction is applied to the traces returned for plotting.
    """
    ref = reference.astype(float)
    observed = capture.astype(float)
    demeaned = observed - observed.mean()
    length = len(ref)
    if np.linalg.norm(demeaned) == 0:
        raise ValueError("Cannot align a constant capture")
    support = np.arange(length + 1)
    periodic = np.r_[ref, ref[0]]

    def candidates(starts):
        position = (starts[:, None] + np.arange(len(observed))) % length
        values = np.interp(position.ravel(), support, periodic).reshape(position.shape)
        centered = values - values.mean(axis=1, keepdims=True)
        denominator = np.linalg.norm(centered, axis=1) * np.linalg.norm(demeaned)
        correlation = np.divide(
            centered @ demeaned,
            denominator,
            out=np.full(len(starts), -np.inf),
            where=denominator > 0,
        )
        return values, correlation

    coarse_start = np.arange(length, dtype=float)
    _, coarse_r = candidates(coarse_start)
    coarse_peak = int(np.argmax(coarse_r))
    fine_start = coarse_peak + np.arange(-60, 61) * 0.025
    _, fine_r = candidates(fine_start)
    peak = int(np.argmax(fine_r))
    start = float(fine_start[peak] % length)
    position = (start + np.arange(len(observed))) % length
    aligned = np.interp(position, support, periodic)
    correlation = float(np.corrcoef(aligned, observed)[0, 1])
    return aligned, {
        "first_sample_in_awg_record_ns": start,
        "last_sample_in_awg_record_ns": start + len(observed) - 1,
        "window_end_in_awg_record_ns_exclusive": start + len(observed),
        "first_sample_original_csv_time_ns": start - 100,
        "last_sample_original_csv_time_ns": start + len(observed) - 101,
        "pearson_r": correlation,
        "alignment_grid_ns": 0.025,
        "reference_amplitude_multiplier": 1.0,
        "reference_offset_added_mV": 0.0,
    }


def configure_style():
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "STIXGeneral", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "font.size": 12,
            "axes.labelsize": 13,
            "axes.titlesize": 12,
            "axes.linewidth": 1.0,
            "axes.edgecolor": "black",
            "axes.facecolor": "white",
            "figure.facecolor": "white",
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.top": True,
            "ytick.right": True,
            "xtick.labelsize": 11,
            "ytick.labelsize": 11,
            "xtick.major.size": 5,
            "ytick.major.size": 5,
            "xtick.minor.size": 2.8,
            "ytick.minor.size": 2.8,
            "legend.frameon": False,
            "legend.fontsize": 11,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def draw_figure(manifest, reference, adc, output_dir: Path, signal_display_scale: float):
    scale = manifest["adc_mV_per_code"]
    display_scale = np.array([signal_display_scale, 1.0, signal_display_scale, 1.0])
    capture_mV = adc * scale
    displayed_capture_mV = capture_mV * display_scale
    time = np.arange(len(adc)) * manifest["sample_period_ns"]
    aligned = {}
    aligned_pulses = {}
    matches = {}
    for column, channel in enumerate(reference["channels"]):
        values, match = align_time(reference["post_hpf_adc"][:, column], adc[:, channel])
        aligned[int(channel)] = values * scale
        matches[str(channel)] = match
        pulse = reference["noiseless_post_hpf_adc"][:, column] * scale
        position = (match["first_sample_in_awg_record_ns"] + np.arange(len(adc))) % len(pulse)
        aligned_pulses[int(channel)] = np.interp(
            position, np.arange(len(pulse) + 1), np.r_[pulse, pulse[0]]
        )

    configure_style()
    fig = plt.figure(figsize=(8.1, 11.1))
    grid = fig.add_gridspec(7, 1, height_ratios=[0.90, 0.90, 0.18, 1, 1, 1, 1], hspace=0.12)
    fig.subplots_adjust(left=0.105, right=0.975, bottom=0.064, top=0.875)
    overview_axes = []
    pulse_positions = {}
    for column, channel in enumerate(reference["channels"]):
        overview = fig.add_subplot(grid[column], sharex=overview_axes[0] if overview_axes else None)
        overview_axes.append(overview)
        overview.set(xlim=(0, 1000), ylim=(-12, 12))
        overview.xaxis.set_major_locator(MultipleLocator(200))
        overview.xaxis.set_minor_locator(AutoMinorLocator(4))
        overview.yaxis.set_major_locator(MultipleLocator(10))
        overview.yaxis.set_minor_locator(AutoMinorLocator(2))
        overview.grid(which="major", color="0.87", linewidth=0.6)
        start = matches[str(channel)]["first_sample_in_awg_record_ns"]
        overview.axvspan(start, start + 256, color=RECORDED, alpha=0.15, linewidth=0, zorder=0)
        noisy_mV = reference["input_volts"][:, column] * 1000 * display_scale[channel]
        pulse_mV = reference["noiseless_input_volts"][:, column] * 1000 * display_scale[channel]
        overview.plot(reference["time_ns"], noisy_mV, color=AWG_INPUT, linewidth=0.65, zorder=2)
        overview.plot(reference["time_ns"], pulse_mV, color="black", linewidth=1.25, zorder=3)
        support = np.flatnonzero(pulse_mV != 0)
        pulse_start, pulse_end = reference["time_ns"][[support[0], support[-1]]]
        pulse_positions[str(channel)] = {
            "first_nonzero_sample_in_awg_record_ns": float(pulse_start),
            "last_nonzero_sample_in_awg_record_ns": float(pulse_end),
        }
        overview.text(0.015, 0.94, f"Ch{channel}: full AWG waveform", transform=overview.transAxes,
                      ha="left", va="top", fontsize=11,
                      bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0}, zorder=5)
        if column == 0:
            overview.tick_params(labelbottom=False)
    overview_axes[0].set_title("Full 1000 ns input and noiseless signal", pad=7)
    overview_axes[-1].set_xlabel("Time from first AWG sample (ns)", fontsize=11, labelpad=1)

    axes = []
    for channel in range(4):
        ax = fig.add_subplot(grid[channel + 3], sharex=axes[0] if axes else None)
        axes.append(ax)
        if channel in aligned:
            ax.plot(time, aligned[channel] * display_scale[channel], color=REFERENCE, linewidth=1.25, zorder=2)
        ax.plot(time, displayed_capture_mV[:, channel], color=RECORDED, linewidth=1.0, zorder=3)
        if channel in aligned_pulses:
            ax.plot(time, aligned_pulses[channel] * display_scale[channel],
                    color="black", linewidth=1.25, zorder=4)
        ax.set(xlim=(0, 255), ylim=(-12, 12))
        ax.xaxis.set_major_locator(MultipleLocator(50))
        ax.xaxis.set_minor_locator(AutoMinorLocator(5))
        ax.yaxis.set_major_locator(MultipleLocator(10))
        ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        ax.grid(which="major", color="0.87", linewidth=0.6)
        injected = "signal injected" if channel in aligned else "no signal injected"
        ax.text(0.015, 0.94, f"({chr(97 + channel)}) Ch{channel}: {injected}",
                transform=ax.transAxes, ha="left", va="top", fontsize=11,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0}, zorder=5)
        if channel in aligned:
            ax.text(0.985, 0.94, rf"$r = {matches[str(channel)]['pearson_r']:.4f}$",
                    transform=ax.transAxes, ha="right", va="top", fontsize=11,
                    bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0}, zorder=5)
        if channel != 3:
            ax.tick_params(labelbottom=False)
    axes[-1].set_xlabel("Time within recorded window (ns)")
    fig.text(0.025, 0.48, "Displayed voltage (mV)", rotation=90, ha="center", va="center", fontsize=14)
    fig.suptitle("AWG injection and FPGA readout", x=0.54, y=0.983, fontsize=16)
    fig.legend(
        handles=[
            Line2D([], [], color=REFERENCE, linewidth=1.8, label="Post-HPF reference"),
            Line2D([], [], color=RECORDED, linewidth=1.8, label="Recorded waveform"),
        ],
        loc="upper center", bbox_to_anchor=(0.54, 0.958), ncol=2,
        handlelength=2.5, columnspacing=1.4, borderaxespad=0,
    )
    fig.legend(
        handles=[
            Line2D([], [], color=AWG_INPUT, linewidth=1.2, label="AWG input (signal + noise)"),
            Line2D([], [], color="black", linewidth=1.5, label="Noiseless signal"),
            Patch(facecolor=RECORDED, alpha=0.2, edgecolor="none", label="Recorded chunk"),
        ],
        loc="upper center", bbox_to_anchor=(0.54, 0.927), ncol=3,
        handlelength=1.7, columnspacing=1.2, borderaxespad=0, fontsize=10,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / "awg_trigger_readout_four_channels"
    fig.savefig(stem.with_suffix(".pdf"), metadata={"Title": "AWG injection and FPGA readout"})
    fig.savefig(stem.with_suffix(".svg"))
    fig.savefig(stem.with_suffix(".png"), dpi=300)
    plt.close(fig)

    summary = {
        "event": manifest["event"],
        "event_timestamp_unconverted": manifest["event_timestamp"],
        "capture_samples_per_channel": len(adc),
        "capture_sample_period_ns": manifest["sample_period_ns"],
        "plotted_capture_channels": [0, 1, 2, 3],
        "reference_channels": [0, 2],
        "capture_AC_RMS_mV": capture_mV.std(axis=0).tolist(),
        "capture_mean_mV": capture_mV.mean(axis=0).tolist(),
        "display_amplitude_multiplier_by_channel": display_scale.tolist(),
        "displayed_capture_AC_RMS_mV": displayed_capture_mV.std(axis=0).tolist(),
        "displayed_reference_AC_RMS_mV": {
            str(channel): float((values * display_scale[channel]).std())
            for channel, values in aligned.items()
        },
        "matches": matches,
        "overview": {
            "waveform": "Complete noisy AWG input before ADC quantization and HPF",
            "pulse": "User-supplied 2rms noiseless estimate, before HPF",
            "pulse_source_constant_multiplier": manifest["source_template_constant_multiplier"],
            "ch2_cyclic_delay_samples": manifest["ch2_cyclic_delay_samples"],
            "pulse_support": pulse_positions,
            "display_scale_matches_readout_panels": True,
            "recorded_chunk_shading": "256 ns interval from the existing post-HPF time alignment",
        },
        "readout_pulse_overlay": {
            "channels": [0, 2],
            "waveform": "Estimated noiseless pulse after the same ADC quantization and fixed-point HPF as the noisy reference",
            "hpf_parameters_sha256": manifest["data_files"]["hpf_lookahead.json"]["sha256"],
            "hpf_model_validation": "Exact ADC-code reproduction of both saved noisy reference channels",
            "hpf_initial_state": "Zero at the start of the full 1000-sample trace, as in the saved noisy reference",
            "alignment": "Existing noisy post-HPF reference time shifts; periodic linear interpolation",
            "display_scale_matches_other_traces": True,
            "used_for_alignment_or_correlation": False,
        },
        "voltage_scale": "Signed ADC code * 800 / 4096 mV, before CNN gain",
        "amplitude_processing": f"Ch0 and Ch2 reference and recording multiplied by {signal_display_scale:.2f} for display; Ch1 and Ch3 unchanged. Source data and alignment remain unscaled; no amplitude fitting, DC subtraction or RMS normalization.",
        "alignment": "Independent channel shifts fitted only from the noisy post-HPF reference; the same shifts and periodic linear interpolation are applied to the filtered pulse",
        "timing_note": "The 0.025 ns search grid is not a timing-accuracy measurement. No exact trigger-decision time is inferred.",
        "software_versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    (output_dir / "awg_trigger_readout_alignment.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "figures")
    parser.add_argument("--signal-display-scale", type=float, default=DEFAULT_SIGNAL_DISPLAY_SCALE,
                        help="Display multiplier for both reference and recording in Ch0 and Ch2")
    args = parser.parse_args()
    if not np.isfinite(args.signal_display_scale) or args.signal_display_scale <= 0:
        parser.error("--signal-display-scale must be finite and positive")
    manifest, reference, adc = load_data(args.data_dir)
    draw_figure(manifest, reference, adc, args.output_dir, args.signal_display_scale)


if __name__ == "__main__":
    main()
