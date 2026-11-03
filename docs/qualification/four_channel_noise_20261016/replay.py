#!/usr/bin/env python3
"""Replay the supplied four-channel capture through qualified native CNN tools.

This is an analysis driver, not a production RTL/configuration change.
All sliding positions are diagnostic; they do not extend capture exposure.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import numpy as np


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--csv', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--verilator-cache', required=True, type=Path)
    ap.add_argument('--sample-rate', type=float, default=1e9)
    a = ap.parse_args()
    root, out, cache = a.root.resolve(), a.output.resolve(), a.verilator_cache.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root/'scripts'))
    import build_native_reference as cal
    source_ref = root/'build/channel_gain_offset_20261015/reference-local'
    ref = json.loads((source_ref/'reference.json').read_text())
    assert ref['cnn_input_gain_ppm'] == cal.GAIN_PPM.tolist()
    assert ref['cnn_input_baseline_uv'] == cal.BASELINE_UV.tolist()
    firmware = root.parent/'CNN-Core-Generator/cnn_core/firmware'
    for name, digest in ref['firmware'].items():
        assert sha(firmware/name) == digest, name
    assert sha(root/'scripts/native_reference.cpp') == ref['harness_sha256']
    closure = json.loads((root/'build/channel_gain_offset_20261015/source_manifest.json').read_text())
    checked = []
    for entry in closure['files']:
        assert sha(Path(entry['resolved_path'])) == entry['sha256'], entry['file']
        if entry['package'] == 'cnn-core':
            assert sha(cache/entry['file']) == entry['sha256'], entry['file']
            checked.append(entry['file'])
    assert sha(cache/'tb_native_core.sv') == sha(root/'tests/tb_native_core.sv')
    data = np.loadtxt(a.csv, delimiter=',', skiprows=1)
    assert data.ndim == 2 and data.shape[1] == 4 and np.isfinite(data).all()
    # The physical CSV lies on the exporter's 800 mV / 4094-code lattice.
    # At these amplitudes 4096 also recovers identical original integer codes.
    raw = np.rint(data*4094/800).astype(np.int16)
    residual = float(abs(data-raw.astype(float)*800/4094).max())
    assert residual < 1e-5 and raw.min() >= -2048 and raw.max() <= 2047
    assert np.array_equal(raw, np.rint(data*4096/800))
    np.savetxt(out/'raw_codes.csv', raw, fmt='%d', delimiter=',', header='ch0,ch1,ch2,ch3', comments='')
    starts = np.arange(len(raw)-256+1)
    windows = np.stack([raw[i:i+256] for i in starts])
    allref = out/'sliding_reference'
    allref.mkdir()
    cal.adc_to_normalized(windows).astype('<f4').tofile(allref/'features.f32')
    shutil.copytree(source_ref/'weights', allref/'weights')
    with (allref/'csim.log').open('w') as log:
        subprocess.run([str(source_ref/'native_reference'), str(len(windows))], cwd=allref,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f'PASS HLS C++ {len(windows)} sliding windows', flush=True)
    words = (allref/'all_input.hex').read_text().splitlines()
    scores_hex = (allref/'all_expected.hex').read_text().splitlines()
    assert len(words) == len(windows)*32 and len(scores_hex) == len(windows)
    codes = np.loadtxt(allref/'adc_conversion.txt', dtype=np.int64).reshape(4,4096)
    # Each packed slot must be the public ADC-to-native conversion, in time/ch order.
    for start in range(len(windows)):
        wanted = codes[np.arange(4)[None,:], windows[start].astype(int)+2048].reshape(-1)
        actual = []
        for word in words[start*32:(start+1)*32]:
            packed = int(word,16)
            actual.extend(((packed>>(16*i))&65535) for i in range(32))
        actual = np.array(actual,dtype=np.int64)
        actual[actual>=32768] -= 65536
        assert np.array_equal(actual,wanted), start
    batches = []
    for first in range(0,len(windows),1096):
        count = min(1096,len(windows)-first)
        batch = out/f'rtl_batch_{first:05d}'
        batch.mkdir()
        (batch/'all_input.hex').write_text('\n'.join(words[first*32:(first+count)*32])+'\n')
        (batch/'all_expected.hex').write_text('\n'.join(scores_hex[first:first+count])+'\n')
        with (batch/'verilator.log').open('w') as log:
            subprocess.run([str(cache/'obj_dir/Vtb_native_core'),f'+REFERENCE={batch}',f'+WINDOWS={count}'],
                           cwd=cache, stdout=log, stderr=subprocess.STDOUT, check=True)
        assert f'PASS Verilator native core: {count} exact scores' in (batch/'verilator.log').read_text()
        batches.append({'first_start':first,'windows':count,'log_sha256':sha(batch/'verilator.log')})
        print(f'PASS RTL starts {first}..{first+count-1}', flush=True)
    score_raw = np.array([int(s,16) for s in scores_hex],dtype=np.int64)
    score_raw[score_raw >= (1<<20)] -= 1<<21
    scores = score_raw/1024.0
    with (out/'scores.csv').open('w') as f:
        w=csv.writer(f,lineterminator="\n"); w.writerow(['start_sample','native_signed_code','score','phase_mod256'])
        w.writerows(zip(starts,score_raw,scores,starts%256))
    stream_indices = starts[::256]
    stream_scores = scores[::256]
    stream = out/'stream_reference'; stream.mkdir()
    (stream/'all_expected.hex').write_text('\n'.join(scores_hex[i] for i in stream_indices)+'\n')
    (stream/'all_input.hex').write_text('\n'.join(words[i*32+b] for i in stream_indices for b in range(32))+'\n')
    with (stream/'adc.hex').open('w') as f:
        for start in stream_indices:
            for beat in raw[start:start+256].reshape(64,4,4):
                eight = np.concatenate([beat.T,np.zeros((4,4),dtype=np.int16)],axis=0)
                packed=sum((int(v)&4095)<<(12*i) for i,v in enumerate(eight.flat))
                f.write(f'{packed:096x}\n')
    files = {n:sha(stream/n) for n in ['adc.hex','all_input.hex','all_expected.hex']}
    (stream/'reference.json').write_text(json.dumps({'windows':len(stream_indices),'files':files},indent=2)+'\n')
    duration = len(stream_indices)*256/a.sample_rate
    scan=[]
    for word in range(int(np.floor(scores.min()*16))-1,int(np.ceil(scores.max()*16))+2):
        threshold=word/16
        count=int(np.count_nonzero(stream_scores>threshold))
        scan.append([word,threshold,count,count/duration,int(np.count_nonzero(scores>threshold))])
    with (out/'threshold_scan.csv').open('w') as f:
        w=csv.writer(f,lineterminator="\n");w.writerow(['threshold_word','threshold','phase0_events','phase0_replay_rate_hz','sliding_positions_above']);w.writerows(scan)
    requested=[]
    for threshold in [7.2,115/16,116/16]:
        phase_counts=[int(np.count_nonzero(scores[phase::256]>threshold)) for phase in range(256)]
        phase_sizes=[len(scores[phase::256]) for phase in range(256)]
        rates=[n/(k*256/a.sample_rate) for n,k in zip(phase_counts,phase_sizes)]
        requested.append({'threshold':threshold,'phase0_events':phase_counts[0],
                          'phase0_replay_rate_hz':rates[0], 'sliding_positions_above':int(np.count_nonzero(scores>threshold)),
                          'phase_events_min_max':[min(phase_counts),max(phase_counts)],
                          'phase_replay_rate_min_max_hz':[min(rates),max(rates)]})
    summary = {
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'csv':str(a.csv),'csv_sha256':sha(a.csv),'samples_per_channel':len(raw),'channels':4,
        'sample_rate_hz_user_confirmed':a.sample_rate,'capture_seconds':len(raw)/a.sample_rate,
        'raw_code_lattice_mV':800/4094,'lattice_max_residual_mV':residual,
        'mean_mV':data.mean(0).tolist(),'demeaned_rms_mV':data.std(0).tolist(),
        'gain_ppm':cal.GAIN_PPM.tolist(),'baseline_uv':cal.BASELINE_UV.tolist(),
        'post_calibration_mean_model_units':cal.adc_to_normalized(raw).mean(0).tolist(),
        'post_calibration_rms_ac_model_units':cal.adc_to_normalized(raw).std(0).tolist(),
        'stream_windows':len(stream_indices),'stream_seconds':duration,'unused_tail_samples':len(raw)-len(stream_indices)*256,
        'sliding_windows':len(scores),'sliding_windows_are_correlated':True,
        'native_score_scale':1024,'threshold_word_scale':16,'comparison':'strict signed score > threshold',
        'phase0_score_min_max':[float(stream_scores.min()),float(stream_scores.max())],
        'sliding_score_min_max':[float(scores.min()),float(scores.max())],
        'sliding_score_quantiles':{str(p):float(np.quantile(scores,p)) for p in [0,.5,.9,.95,.99,.999,1]},
        'threshold_results':requested,'phase0_min_word_zero_events':int(np.ceil(stream_scores.max()*16)),
        'all_phases_min_word_zero_events':int(np.ceil(scores.max()*16)),
        'phase0_rate_resolution_hz':1/duration,
        'zero_events_poisson_95pct_upper_hz':float(-np.log(.05)/duration),
        'target_1hz_probability_per_window':256/a.sample_rate,
        'target_1hz_expected_count_in_capture':duration,
        'native_hls_binary_sha256':sha(source_ref/'native_reference'),
        'verilator_binary_sha256':sha(cache/'obj_dir/Vtb_native_core'),
        'cached_native_files_verified':checked,'rtl_batches':batches,
        'data_reuse_warning':'The capture statistics match the data used to choose these gains/baselines; this is an in-sample replay, not a new calibration-stability test.'
    }
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
