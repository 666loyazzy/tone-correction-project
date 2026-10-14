"""Import the OU course's four-tone clip, trim syllables, and extract real reference curves."""
import argparse
import json
from pathlib import Path
import sys
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.acoustics import decode_audio, analyze
from backend.scoring import normalized_curve

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True,help='Downloaded l197-pronunciation-10a.mp3 from the official course')
parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
args=parser.parse_args()
directory=args.data_dir/'references';directory.mkdir(parents=True,exist_ok=True)
samples,rate=decode_audio(args.source,directory/'openlearn-four-tones-original.wav')
source='https://www.open.edu/openlearn/mod/oucontent/view.php?id=106435&section=2'
catalog=[]
for tone,(start,end) in enumerate([(0.88,1.73),(3.35,3.97),(5.70,6.76),(8.25,8.79)],1):
    ref_id=f'openlearn-ma-{tone}';folder=directory/ref_id;folder.mkdir(exist_ok=True)
    selected=samples[round(start*rate):round(end*rate)]
    with wave.open(str(folder/'audio.wav'),'wb') as output:
        import numpy as np
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate)
        output.writeframes((np.clip(selected,-1,1)*32767).astype('<i2').tobytes())
    result=analyze(selected,rate,tone,75,600,None,None)
    assert result['features'] and result['quality']['valid_frames']>=8,(tone,result['quality'])
    catalog.append({**normalized_curve(result),'id':ref_id,'tone':tone,'kind':'professional',
        'name':'开放大学示范 · '+['mā','má','mǎ','mà'][tone-1],'version':'openlearn-2020-1',
        'source_url':source,'audio_url':f'/api/references/{ref_id}/audio',
        'note':'英国开放大学 OpenLearn《Getting started with Chinese 1》原声，截取单个音节；Praat 实际提取。',
        'attribution':'Getting started with Chinese 1. Reworked from The Open University, copyright 2020.',
        'license':'CC BY-NC-SA 4.0','license_url':'https://creativecommons.org/licenses/by-nc-sa/4.0/',
        'license_source':'https://www.open.edu/openlearn/mod/oucontent/view.php?id=106436&section=__acknowledgements',
        'original_audio_url':'https://www.open.edu/openlearn/pluginfile.php/2651906/mod_oucontent/oucontent/99866/c003f2d9/c6133324/l197-pronunciation-10a.mp3?forcedownload=1',
        'source_segment_s':[start,end],'time_s':result['time_s'],'f0_hz':result['f0_hz'],
        'algorithm':result['algorithm'],'quality':result['quality']})
    print(json.dumps({'tone':tone,'valid_frames':result['quality']['valid_frames'],
                      'start_hz':result['features']['start_f0_hz'],'end_hz':result['features']['end_f0_hz']},ensure_ascii=False))
(ROOT/'backend'/'reference_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2),encoding='utf-8')
(directory/'ATTRIBUTION.txt').write_text('The Open University, Getting started with Chinese 1 (2020).\n'+source+'\nCC BY-NC-SA 4.0\nhttps://creativecommons.org/licenses/by-nc-sa/4.0/\nChanges: mono WAV decoding and extraction of four individual syllables.\n',encoding='utf-8')
