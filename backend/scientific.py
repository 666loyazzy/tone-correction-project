"""Measured evidence and a reproducible teaching experiment, before model interpretation."""
from __future__ import annotations

import math
import numpy as np
from .exercises import pronunciation


TARGETS = {
    1: '孤立一声关注相对平稳的高平走势；绝对 Hz 随个人音域变化。',
    2: '孤立二声重点观察后段上升及其时间分布，结合起止音高和局部走势。',
    3: '本任务是孤立三声，观察低音区、下降和回升的相对关系；连续语流需另按语境分析。',
    4: '孤立四声关注从高到低的下降及其时间分布，保持舒适发声。',
}
EXPERIMENTS = {
    1: '固定麦克风距离和音量，连续录三次 mā。一次只调整发音的平稳程度；比较首尾半音差、音高范围及有效帧分布。',
    2: '固定录音设置，连续录三次 má。先以舒适速度完整读，再只调整后段上升的时间分布；比较中、后段中位 F0 与首尾半音差。',
    3: '固定录音设置，连续录三次孤立 mǎ。先完整读，再只调整低音区与回升的衔接；比较到最低点的降幅、回升幅度和最低点相对位置。',
    4: '固定录音设置，连续录三次 mà。一次只调整下降的连贯性；比较前、中、后段中位 F0 和首尾半音差。',
}


def evidence(result: dict | None, previous: dict | None = None) -> dict | None:
    if result is None:
        return None
    from .harness import validate_chart
    validate_chart(result)
    quality = result['quality']
    features = result['features']
    total = quality['total_frames']
    output = {
        'tone': result['tone'], 'syllable':result.get('syllable','ma'), 'selection': result['selection'],
        'algorithm': result['algorithm'], 'features': features,
        'quality': {**quality, 'voiced_percent': round(100 * quality['valid_frames'] / total, 1) if total else 0},
        'target': TARGETS[result['tone']], 'regions': [], 'comparison': None,
        'experiment': EXPERIMENTS[result['tone']].replace(['mā','má','mǎ','mà'][result['tone']-1],
            pronunciation(result.get('syllable','ma'),result['tone'])),
        'interpretation_scope': '同一人的声学观测与可重复练习；无教师参考音、说话者音域校准或评分模型。最低 F0 的位置不是语言学 TP 的自动标注。',
        'minimum_position_definition': '最低点相对位置=(最低有效 F0 时间-首个有效 F0 时间)/(末个有效 F0 时间-首个有效 F0 时间)，分母不是整个选区时长。',
        'score':result.get('score'), 'score_change':None,
    }
    times = np.asarray(result['time_s'])
    start, end = result['selection']['start_s'], result['selection']['end_s']
    values = np.asarray([np.nan if value is None else value for value in result['f0_hz']])
    for index, label in enumerate(['前段', '中段', '后段']):
        lo, hi = start + (end-start)*index/3, start + (end-start)*(index+1)/3
        selected = values[(times >= lo) & (times < hi if index < 2 else times <= hi)]
        valid = selected[np.isfinite(selected)]
        output['regions'].append({'label': label, 'start_s': round(lo, 4), 'end_s': round(hi, 4),
            'valid_frames': len(valid), 'total_frames': len(selected),
            'median_f0_hz': round(float(np.median(valid)), 3) if len(valid) >= 3 else None})
    if features:
        output['pitch_range_semitones'] = round(12*math.log2(features['max_f0_hz']/features['min_f0_hz']), 3)
        output['drop_to_minimum_semitones'] = round(12*math.log2(features['start_f0_hz']/features['min_f0_hz']), 3)
        output['rise_from_minimum_semitones'] = round(12*math.log2(features['end_f0_hz']/features['min_f0_hz']), 3)
    if previous and previous['result']['tone'] == result['tone'] and previous['result'].get('syllable','ma')==result.get('syllable','ma'):
        prior = previous['result']
        prior_features = prior['features']
        matching = all(prior['algorithm'].get(key) == result['algorithm'].get(key)
                       for key in ('method', 'parselmouth_version', 'pitch_floor_hz', 'pitch_ceiling_hz'))
        output['comparison'] = {'practice_id': previous['id'], 'created_at': previous['created_at'],
            'matching_parameters': matching, 'previous_features': prior_features,
            'previous_quality': prior['quality'], 'changes': {}}
        if features and prior_features and matching:
            for key in ('selected_duration_s', 'end_minus_start_semitones', 'minimum_position'):
                output['comparison']['changes'][key] = round(features[key] - prior_features[key], 4)
        current_score=result.get('score');prior_score=prior.get('score')
        if current_score and prior_score and current_score['comparison_key']==prior_score['comparison_key'] and current_score['total'] is not None and prior_score['total'] is not None:
            output['previous_score']=prior_score['total']
            output['score_change']=round(current_score['total']-prior_score['total'],1)
    return output


def local_feedback(measured: dict | None) -> str:
    if measured is None:
        return '先选择目标声调，以舒适音量录一个完整音节，再点击分析。我会结合曲线说明你的升降特点，并给出下一轮的具体练习动作。'
    q, f, tone = measured['quality'], measured['features'], measured['tone']
    if not f:
        return '本次有效基频不足。重新截取一个完整音节，固定麦克风距离，用舒适音量再录一次；看到连续的有效曲线后，重点观察前、中、后段的升降。'
    delta=f['end_minus_start_semitones']
    if tone == 3:
        if f['min_f0_hz'] < min(f['start_f0_hz'], f['end_f0_hz']):
            point='这次发音已经形成先降后升的轮廓。下一轮把低音区到收尾的回升连起来，保持一个完整音节。'
        elif delta > 0:
            point='这次发音的主要走势是上升。练孤立三声时，先把前段降下来，再从低音区连贯回升。'
        elif delta < 0:
            point='这次下降延伸到了收尾。下一轮在低音区后接上回升，让孤立三声的降升轮廓完整。'
        else:
            point='这次首尾音高接近。下一轮把前段下降、低音区和后段回升依次连起来。'
    elif tone == 2:
        point=('这次末尾高于起点，已经形成二声的上升走势。下一轮把中段到末尾的上升连贯读完。' if delta > 0
               else '这次收尾没有高于起点。下一轮保持起音舒适，把中段到末尾连续向上带，读完整个二声。')
    elif tone == 4:
        point=('这次末尾低于起点，下降方向符合四声的练习目标。下一轮把下降贯穿整个音节。' if delta < 0
               else '这次收尾没有低于起点。下一轮从舒适的高起点连续降下来，把四声读完整。')
    else:
        point=f"这次音高跨度为 {measured['pitch_range_semitones']:.2f} 半音。下一轮稳定起音到收尾的音高，把一声连贯读平。"
    regions='；'.join(f"{r['label']}中位音高 {r['median_f0_hz']:.1f} Hz" if r['median_f0_hz'] is not None
                     else f"{r['label']}有效 F0 不足" for r in measured['regions'])
    observation=(f"看曲线：起始 {f['start_f0_hz']:.1f} Hz，结束 {f['end_f0_hz']:.1f} Hz，首尾差 {delta:+.2f} 半音。"
                 f"{regions}。最低点位于首尾有效 F0 时间跨度的 {f['minimum_position']*100:.1f}%。")
    if q['warnings']:
        observation+='录音调整：'+'；'.join(q['warnings'])+'。'
    comparison = measured['comparison']
    if comparison and comparison['changes']:
        c = comparison['changes']
        observation+=(f"相比上次，首尾差变化 {c['end_minus_start_semitones']:+.2f} 半音，"
                      f"最低点相对位置变化 {c['minimum_position']*100:+.1f} 个百分点；两张曲线直接展示升降幅度与衔接位置的变化。")
    elif comparison:
        observation+='统一两次的提取参数和发音选区，再比较升降幅度与衔接位置。'
    else:
        observation+='保留这次曲线，作为下一次同声调练习的对照。'
    score=measured.get('score') or {}
    if score.get('total') is not None:
        point=f"本次声调轮廓分 {score['total']:.1f} 分。"+point
        if measured.get('score_change') is not None:point+=f"较上次同参照练习 {measured['score_change']:+.1f} 分。"
    return '\n\n'.join([point, observation, '下一轮这样练：'+measured['experiment']])
