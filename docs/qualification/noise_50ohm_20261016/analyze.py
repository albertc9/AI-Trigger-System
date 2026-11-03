#!/usr/bin/env python3
"""Read-only four-capture calibration audit; production constants stay fixed."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import numpy as np


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',required=True,type=Path)
    ap.add_argument('--input-dir',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args();root=a.root.resolve();out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(root/'scripts'))
    import build_native_reference as cal
    prior=root/'build/channel_gain_offset_20261015/reference-local'
    reference=json.loads((prior/'reference.json').read_text())
    assert reference['cnn_input_gain_ppm']==cal.GAIN_PPM.tolist()
    assert reference['cnn_input_baseline_uv']==cal.BASELINE_UV.tolist()
    assert sha(prior/'adc_conversion.txt')==reference['files']['adc_conversion.txt']
    conversion=np.loadtxt(prior/'adc_conversion.txt',dtype=np.int64).reshape(4,4096)
    closure=json.loads((root/'build/channel_gain_offset_20261015/source_manifest.json').read_text())
    for entry in closure['files']:
        assert sha(Path(entry['resolved_path']))==entry['sha256'],entry['file']
    packages=['CNN_INPUT_GAIN_PKG.vhd','CNN_INPUT_OFFSET_PKG.vhd','CNN_INPUT_CALIBRATION_PKG.vhd']
    for name in packages:
        assert sha(root/'HDL/rtl'/name)==sha(root.parent/'gateware/sources/ai-trigger/rtl/ai-trigger'/name)
    rows=[];sources=[];arrays=[]
    for run in range(4):
        p=a.input_dir/f'all_channels_ampl_50Ohm_{run}.csv'
        with p.open() as f:
            assert next(csv.reader(f))==['ch0_mV','ch1_mV','ch2_mV','ch3_mV']
        x=np.loadtxt(p,delimiter=',',skiprows=1)
        assert x.ndim==2 and x.shape[1]==4 and np.isfinite(x).all()
        n=len(x);raw=np.rint(x*4094/800).astype(np.int64)
        reconstruction=float(abs(x-raw.astype(float)*800/4094).max())
        assert reconstruction<1e-5 and raw.min()>=-2048 and raw.max()<=2047
        assert np.array_equal(raw,np.rint(x*4096/800))
        model=cal.adc_to_normalized(raw)
        q=conversion[np.arange(4)[None,:],raw+2048]
        assert np.array_equal(q,np.clip(np.floor(model*32+.5),-511,511))
        native=q/32.0
        mean=x.mean(0);sigma=x.std(0);total=np.sqrt(np.mean(x*x,axis=0))
        for ch in range(4):
            # Independent high-accuracy reduction of the actual CSV values.
            column=x[:,ch].tolist()
            mean_check=math.fsum(column)/n
            variance_check=math.fsum((v-mean_check)**2 for v in column)/n
            assert abs(mean[ch]-mean_check)<1e-12
            assert abs(sigma[ch]-math.sqrt(variance_check))<1e-11
            # Independent integer histogram verifies the exact quantized moments.
            counts=np.bincount(raw[:,ch]+2048,minlength=4096)
            codes=conversion[ch]
            first=sum(int(c)*int(k) for c,k in zip(codes,counts))
            second=sum(int(c)*int(c)*int(k) for c,k in zip(codes,counts))
            qmean=first/n/32
            qstd=math.sqrt((second*n-first*first)/(n*n))/32
            assert abs(native[:,ch].mean()-qmean)<1e-12
            assert abs(native[:,ch].std()-qstd)<1e-12
            row={
                'record':run,'channel':ch,'samples':n,
                'offset_mV':float(mean[ch]),'rms_ac_mV':float(sigma[ch]),
                'rms_total_mV':float(total[ch]),'fixed_gain':float(cal.GAINS[ch]),
                'fixed_offset_mV':float(cal.BASELINE_UV[ch]/1000),
                'reference_rms_ac_mV':float(12.5/cal.GAINS[ch]),
                'offset_error_mV':float(mean[ch]-cal.BASELINE_UV[ch]/1000),
                'after_gain_dc_mV':float((mean[ch]-cal.BASELINE_UV[ch]/1000)*cal.GAINS[ch]),
                'after_gain_rms_ac_mV':float(sigma[ch]*cal.GAINS[ch]),
                'physical_normalized_mean':float((mean[ch]-cal.BASELINE_UV[ch]/1000)*cal.GAINS[ch]/12.5),
                'physical_normalized_rms_ac':float(sigma[ch]*cal.GAINS[ch]/12.5),
                'native_normalized_mean':qmean,'native_normalized_rms_ac':qstd,
                'native_normalized_rms_total':math.sqrt(second/n)/32,
                'native_rms_error_percent':100*(qstd-1),
                'quantization_error_rms_model_units':float(np.sqrt(np.mean((native[:,ch]-model[:,ch])**2))),
                'raw_adc_min':int(raw[:,ch].min()),'raw_adc_max':int(raw[:,ch].max()),
                'native_saturation_samples':int(np.count_nonzero(abs(q[:,ch])==511)),
                'adc_endpoint_samples':int(np.count_nonzero((raw[:,ch]==-2048)|(raw[:,ch]==2047)))
            }
            assert abs(total[ch]**2-mean[ch]**2-sigma[ch]**2)<1e-10
            rows.append(row)
        sources.append({'record':run,'path':str(p),'sha256':sha(p),'samples_per_channel':n,
                        'lattice_max_residual_mV':reconstruction})
        arrays.append(x)
    metadata={
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'inputs':sources,'gain_ppm':cal.GAIN_PPM.tolist(),'baseline_uv':cal.BASELINE_UV.tolist(),
        'rms_definition':'population AC RMS = sqrt(mean((x-mean(x))^2)), ddof=0',
        'offset_definition':'arithmetic mean of each full channel recording, in CSV mV',
        'native_definition':'q=clamp(floor((raw-baseline_codes)*gain/2+0.5),-511,511); normalized=q/32',
        'normalization_target':{'mean':0,'rms_ac':1,'physical_rms_after_gain_mV':12.5},
        'total_samples_per_channel':sum(len(x) for x in arrays),
        'native_channel_samples_checked':sum(x.size for x in arrays),
        'source_closure_entries_checked':len(closure['files']),
        'independent_checks':['math.fsum CSV means and variances','integer histogram native means and variances','native HLS quantization conversion table','Trigger/gateware package hash equality'],
        'calibration_source_hashes':{name:sha(root/'HDL/rtl'/name) for name in packages},
        'native_conversion_table_sha256':sha(prior/'adc_conversion.txt'),
        'rows':rows
    }
    (out/'results.json').write_text(json.dumps(metadata,indent=2)+'\n')
    with (out/'channel_statistics.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    print('| Record | Ch | Offset mV | AC RMS mV | Offset error mV | Native mean | Native AC RMS | RMS error % |')
    print('| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |')
    for r in rows:
        print(f"| {r['record']} | {r['channel']} | {r['offset_mV']:+.6f} | {r['rms_ac_mV']:.6f} | {r['offset_error_mV']:+.6f} | {r['native_normalized_mean']:+.6f} | {r['native_normalized_rms_ac']:.6f} | {r['native_rms_error_percent']:+.4f} |")
    print('PASS:',metadata['native_channel_samples_checked'],'native samples; independent reductions; no production edits')


if __name__=='__main__':
    main()
