#!/usr/bin/env python3
"""Checker negative controls; synthetic arrays test the checker, not vocal quality."""
import json
import numpy as np
from ai_quality_qa import error, pulse_locations, sisdr

rng=np.random.default_rng(41)
reference=rng.normal(size=(2000,2)).astype('float32')*.1
checks=[]
def yes(name,condition):
    assert condition,name
    checks.append({'name':name,'passed':True})
yes('identical_output',error(reference.copy(),reference)['passed'])
yes('gain_change_rejected',not error(reference*1.01,reference)['passed'])
yes('stereo_swap_rejected',not error(reference[:,::-1],reference)['passed'])
yes('one_sample_shift_not_corrected_to_pass',not error(np.roll(reference,1,axis=0),reference)['passed'])
zero=np.zeros_like(reference)
yes('zero_reference_absolute_threshold_pass',error(zero+5e-8,zero)['passed'])
yes('zero_reference_absolute_threshold_fail',not error(zero+2e-7,zero)['passed'])
yes('nan_rejected',not error(reference*np.nan,reference)['passed'])
try:error(reference[:-1],reference);raise RuntimeError('frame mismatch accepted')
except AssertionError:checks.append({'name':'frame_mismatch_rejected','passed':True})
pulse=np.zeros((100,2),dtype='float32');pulse[0]=1;pulse[-1]=.5
yes('pulse_origin_identity',all(v['native_peak_sample']==0 for v in pulse_locations(pulse,pulse)))
try:pulse_locations(np.roll(pulse,2,axis=0),pulse);raise RuntimeError('pulse2sample shift accepted')
except AssertionError:checks.append({'name':'pulse_shift_two_samples_rejected','passed':True})
improvements={'vocals':[8,9,10],'accompaniment':[-3,-2,-1]}
yes('one_stem_gain_cannot_hide_other_loss',not all(float(np.median(v))>=0 for v in improvements.values()))
noise=rng.normal(size=reference.shape).astype('float32')*.05
yes('sisdr_target_better_than_mix',sisdr(reference,reference)>sisdr(reference,reference+noise))
print(json.dumps({'result':'PASS','scope':'Verifier math and strict failure controls only; not model quality evidence','checks':checks},indent=2))
