"""Versioned, deterministic contour matching for isolated-tone practice."""
from __future__ import annotations

import hashlib
import json
import numpy as np

VERSION = 'contour-1.0'
SOURCE = 'https://assets.cambridge.org/97805216/03980/excerpt/9780521603980_excerpt.htm'


def template(tone: int) -> dict:
    digits={1:'55',2:'35',3:'214',4:'51'}[tone]
    # Chao digits specify levels; this illustrative time placement is a teaching setting.
    levels={1:[.5,.5],2:[0.,1.],3:[1/3,0.,1.],4:[1.,0.]}[tone]
    return {'id':f'target-{tone}', 'tone':tone, 'kind':'schematic', 'name':f'教学目标示意 · {digits}',
            'source_url':SOURCE, 'version':'target-1',
            'time_normalized':[0.,.45,1.] if tone==3 else [0.,1.],
            'pitch_normalized':levels, 'audio_url':None,
            'note':'五度标记的教学示意；三声低点的时间位置为本系统练习设定。'}


def normalized_curve(result: dict) -> dict:
    values=np.array([np.nan if value is None else value for value in result['f0_hz']],dtype=float)
    valid=np.isfinite(values)
    times=np.asarray(result['time_s'],dtype=float)
    if valid.sum()<2:return {'time_normalized':[], 'pitch_normalized':[]}
    first,last=np.flatnonzero(valid)[[0,-1]]
    pitch=12*np.log2(values[first:last+1])
    lo,hi=np.nanpercentile(pitch,[10,90])
    center=float(np.nanmedian(pitch))
    span=max(float(hi-lo),2.)
    relative=(pitch-(lo if hi-lo>=2 else center-1))/span
    return {'time_normalized':((times[first:last+1]-times[first])/(times[last]-times[first])).round(6).tolist(),
            'pitch_normalized':[round(float(np.clip(value,0,1)),6) if np.isfinite(value) else None for value in relative]}


def aligned(curve: dict, grid: np.ndarray) -> np.ndarray:
    """Interpolate only within contiguous voiced runs, never across missing frames."""
    times=np.asarray(curve['time_normalized']); values=curve['pitch_normalized']
    output=np.full(len(grid),np.nan); start=0
    while start<len(values):
        if values[start] is None:start+=1;continue
        end=start+1
        while end<len(values) and values[end] is not None:end+=1
        if end-start>=2:
            selected=(grid>=times[start])&(grid<=times[end-1])
            output[selected]=np.interp(grid[selected],times[start:end],values[start:end])
        start=end
    return output


def evaluate(result: dict, reference: dict | None = None) -> dict:
    reference=reference or template(result['tone'])
    digest=hashlib.sha256(json.dumps({'version':VERSION,'reference':reference,
        'algorithm':result['algorithm']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    q=result['quality']; coverage=100*q['valid_frames']/q['total_frames'] if q['total_frames'] else 0.
    score={'version':VERSION, 'comparison_key':digest, 'reference':reference, 'total':None, 'scale_max':100,
           'weights':{'contour':.75,'direction':.25},
           'components':{}, 'curve':normalized_curve(result), 'deviations':[], 'reason':'',
           'method':'轮廓分 = 75% 轮廓贴合 + 25% 升降方向；有效时间和个人相对音高归一化。',
           'scope':'用于孤立声调复练的轮廓匹配指标；分数按参照曲线计算。'}
    if not result['features'] or coverage<50 or q['octave_jumps'] or q['clipped_fraction']>.005:
        score['reason']='先取得连续、清晰的有效声调曲线，再计算轮廓分。';return score
    grid=np.linspace(0,1,41)
    current=aligned(score['curve'],grid); target=aligned(reference,grid)
    usable=np.isfinite(current)&np.isfinite(target)
    if usable.sum()<25:
        score['reason']='有效对照点不足，重新选取完整发音后评分。';return score
    contour=100*max(0.,1-float(np.mean(np.abs(current[usable]-target[usable]))))
    directions=[]
    # Local quarter medians preserve missing segments; use only shared voiced observations.
    medians=[]
    for lo,hi in zip([0,.2,.4,.6,.8],[.2,.4,.6,.8,1.00001]):
        section=usable&(grid>=lo)&(grid<hi)
        medians.append((float(np.median(current[section])),float(np.median(target[section]))) if section.sum()>=2 else None)
    for before,after in zip(medians,medians[1:]):
        if before is None or after is None:continue
        change=after[0]-before[0]; expected=after[1]-before[1]
        directions.append(max(0.,1-abs(change)/.25) if abs(expected)<.06
                          else float(np.clip(change*np.sign(expected)/abs(expected),0,1)))
    if not directions:
        score['reason']='有效分段不足，重新录一个完整音节后评分。';return score
    direction=100*float(np.mean(directions))
    score['components']={'轮廓贴合':round(contour,1),'升降方向':round(direction,1),'有效帧比例':round(coverage,1)}
    score['total']=round(.75*contour+.25*direction,1)
    for index,label in enumerate(['前段','中段','后段']):
        section=usable&(grid>=index/3)&(grid<(index+1)/3 if index<2 else grid<=1)
        score['deviations'].append({'label':label,'error':round(float(np.mean(np.abs(current[section]-target[section]))),4) if section.any() else None})
    return score


def ensure_score(result: dict | None) -> dict | None:
    if result is None or 'score' in result:return result
    return {**result,'score':evaluate(result)}
