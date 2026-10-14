"""PSOLA changes the learner's pitch contour while retaining the recorded syllable."""
from pathlib import Path
import numpy as np
import parselmouth
from parselmouth.praat import call
from .acoustics import PRAAT_LOCK, AudioError
from .scoring import aligned


def resynthesize(source: Path, destination: Path, result: dict):
    score=result['score']
    if score['total'] is None:raise AudioError('请先录制一段有连续基频的完整音节，再播放纠正示范。')
    reference=score['reference'];a=result['algorithm'];selection=result['selection']
    values=np.asarray([np.nan if value is None else value for value in result['f0_hz']])
    valid=np.isfinite(values);times=np.asarray(result['time_s'])[valid]
    relative=(times-times[0])/(times[-1]-times[0])
    target=aligned(reference,relative)
    current=values[valid];center=float(np.median(12*np.log2(current)))
    recorded=reference.get('f0_hz',[])
    pitches=np.array([value for value in recorded if value is not None])
    span=float(np.ptp(np.percentile(12*np.log2(pitches),[10,90]))) if len(pitches) else 6.
    span=float(np.clip(span,2,10))
    target_center=float(np.nanmedian(target))
    desired=np.where(np.isfinite(target),2**((center+(target-target_center)*span)/12),current)
    desired=np.clip(desired,a['pitch_floor_hz'],a['pitch_ceiling_hz'])
    with PRAAT_LOCK:
        sound=parselmouth.Sound(str(source)).extract_part(selection['start_s'],selection['end_s'],preserve_times=False)
        manipulation=call(sound,'To Manipulation',.01,a['pitch_floor_hz'],a['pitch_ceiling_hz'])
        tier=call(manipulation,'Extract pitch tier')
        call(tier,'Remove points between',sound.xmin,sound.xmax)
        for time,frequency in zip(times,desired):
            call(tier,'Add point',float(time-selection['start_s']),float(frequency))
        call([tier,manipulation],'Replace pitch tier')
        corrected=call(manipulation,'Get resynthesis (overlap-add)')
        corrected.scale_peak(.9)
        corrected.save(str(destination),parselmouth.SoundFileFormat.WAV)
