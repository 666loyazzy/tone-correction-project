"""Measured evidence and a reproducible teaching experiment, before model interpretation."""
from __future__ import annotations

import math
import numpy as np


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
        'tone': result['tone'], 'selection': result['selection'],
        'algorithm': result['algorithm'], 'features': features,
        'quality': {**quality, 'voiced_percent': round(100 * quality['valid_frames'] / total, 1) if total else 0},
        'target': TARGETS[result['tone']], 'regions': [], 'comparison': None,
        'experiment': EXPERIMENTS[result['tone']],
        'interpretation_scope': '同一人的声学观测与可重复练习；无教师参考音、说话者音域校准或评分模型。最低 F0 的位置不是语言学 TP 的自动标注。',
        'minimum_position_definition': '最低点相对位置=(最低有效 F0 时间-首个有效 F0 时间)/(末个有效 F0 时间-首个有效 F0 时间)，分母不是整个选区时长。',
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
    if previous and previous['result']['tone'] == result['tone']:
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
    return output


def local_report(measured: dict | None) -> str:
    if measured is None:
        return ('测量准备\n先选择目标声调，录一个完整音节并回听，再点击分析。\n'
                '分析后将提供有效 F0 比例、分段走势、声学特征和同声调复练对比；教师示范音可用于教学参照。')
    a, q, f = measured['algorithm'], measured['quality'], measured['features']
    sections = [
        '测量方法与录音质量\n'
        f"Praat {a['praat_version']}，raw autocorrelation，步长 {a['step_s']} 秒，范围 {a['pitch_floor_hz']}–{a['pitch_ceiling_hz']} Hz。"
        f"选区 {measured['selection']['start_s']:.2f}–{measured['selection']['end_s']:.2f} 秒；有效 F0 {q['valid_frames']}/{q['total_frames']} 帧（{q['voiced_percent']}%）。"
        + ('质量提示：'+'；'.join(q['warnings']) if q['warnings'] else '现有检测未发现录音质量提示。'),
    ]
    if f:
        regions = '；'.join(f"{r['label']}中位 F0 {r['median_f0_hz']:.1f} Hz（{r['valid_frames']}/{r['total_frames']} 帧）"
                           if r['median_f0_hz'] is not None else f"{r['label']}有效 F0 不足" for r in measured['regions'])
        sections.append('本次声学证据\n'
            f"起始 {f['start_f0_hz']:.1f} Hz，结束 {f['end_f0_hz']:.1f} Hz，首尾差 {f['end_minus_start_semitones']:+.2f} 半音；"
            f"最低 {f['min_f0_hz']:.1f} Hz，最高 {f['max_f0_hz']:.1f} Hz。最低点位于首尾有效 F0 时间跨度的 {f['minimum_position']*100:.1f}%。"
            + regions + '。')
        sections.append('目标声调与证据解释\n'+measured['target']+
            f"本次起点到最低点相差 {measured['drop_to_minimum_semitones']:.2f} 半音，最低点到终点相差 {measured['rise_from_minimum_semitones']:.2f} 半音；"
            '结合曲线和回听判断各段是否对应一次完整发音。断线处不进行走势插值。')
    else:
        sections.append('本次声学证据\n有效基频不足以计算稳定特征，保留缺失值；本轮优先改善录音和选区，再讨论声调走势。')
        sections.append('目标声调与证据解释\n'+measured['target']+'本轮先取得足够的有效 F0，再检验该目标的声学走势。')
    comparison = measured['comparison']
    if comparison and comparison['changes']:
        c = comparison['changes']
        text = (f"与本人此前同声调练习 {comparison['practice_id'][:8]} 相比，选区时长变化 {c['selected_duration_s']:+.3f} 秒，"
                f"首尾半音差变化 {c['end_minus_start_semitones']:+.2f} 半音，最低点相对位置变化 {c['minimum_position']*100:+.1f} 个百分点。"
                '这些是变化量；是否更符合目标还需结合回听和教师参考。')
    elif comparison:
        text = '已有此前同声调练习，但参数不同或有效 F0 不足，本次不直接计算特征差值。统一参数和发音选区后复练。'
    else:
        text = '尚无此前同声调的已完成练习。本次作为个人基线，下一次在相同设置下进行对照。'
    sections.append('同声调复练对比\n'+text)
    sections.append('下一轮练习实验\n'+('先回听并处理质量提示，确认一次完整发音的选区。' if q['warnings'] else '')+
                    measured['experiment']+'逐次保存数据，观察变化是否能够重复出现。')
    sections.append('方法依据与适用范围\n'+measured['interpretation_scope']+
                    '测量方法参照 Praat 文档；二声、三声研究提供了考察时间线索的依据，不能把研究实验准确率迁移成本系统成绩。')
    return '\n\n'.join(sections)
