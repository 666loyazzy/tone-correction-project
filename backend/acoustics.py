"""Deterministic acoustic extraction; no speech recognition or clinical inference."""
from __future__ import annotations

import math
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import parselmouth
from .scoring import evaluate

MAX_SECONDS = 30
PRAAT_LOCK = threading.Lock()


class AudioError(ValueError):
    pass


def decode_audio(source: Path, destination: Path) -> tuple[np.ndarray, int]:
    try:
        completed = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-i", str(source), "-vn", "-ac", "1", "-t", str(MAX_SECONDS + 0.1),
             "-c:a", "pcm_s16le", "-threads", "1", "-y", str(destination)],
            capture_output=True, timeout=15, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise AudioError("音频转换未完成，请检查文件或服务器 FFmpeg。") from exc
    if completed.returncode:
        raise AudioError("无法读取音频。请上传完整的 WAV、WebM、M4A、MP4、MP3 或 OGG 文件。")
    try:
        with wave.open(str(destination), "rb") as wav:
            rate = wav.getframerate()
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(float) / 32768
    except (wave.Error, EOFError) as exc:
        raise AudioError("音频解码失败，请重新录音。") from exc
    duration = len(samples) / rate
    if rate < 8000 or duration < 0.15:
        raise AudioError("录音过短或采样率过低，请录制一次完整发音。")
    if duration > MAX_SECONDS:
        raise AudioError(f"每次录音最多 {MAX_SECONDS} 秒，请选取一段较短的音频。")
    return samples, rate


def waveform(samples: np.ndarray, bins: int = 320) -> list[list[float]]:
    return [[round(float(block.min()), 4), round(float(block.max()), 4)]
            for block in np.array_split(samples, min(bins, len(samples))) if len(block)]


def suggested_bounds(samples: np.ndarray, rate: int) -> tuple[float, float]:
    """Energy proposal only. The learner can adjust it; it is not phonetic alignment."""
    size = max(1, round(rate * 0.02))
    rms = np.array([np.sqrt(np.mean(block ** 2)) for block in np.array_split(
        samples, max(1, math.ceil(len(samples) / size))) if len(block)])
    peak = float(rms.max())
    if peak < 0.001:
        return 0.0, len(samples) / rate
    active = np.flatnonzero(rms > max(0.001, peak * 0.08))
    hop = len(samples) / len(rms) / rate
    return max(0.0, float(active[0] * hop) - 0.06), min(
        len(samples) / rate, float((active[-1] + 1) * hop) + 0.06)


def teaching_feedback(tone: int, features: dict | None, quality: dict) -> list[str]:
    if features is None:
        return ["这次录音中没有足够的有效基频，暂时无法描述声调走势。",
                "请靠近麦克风，以舒适音量完整读一次，再回听确认声音清楚。"]
    descriptions = {
        1: "一声练习关注音高是否较平稳。先听示范，再以舒适音高保持平稳。",
        2: "二声练习关注后段的上升。可以配合向上手势，慢速跟读。",
        3: "孤立三声练习关注低音区及降升走势。句子中的三声需要结合语境分析。",
        4: "四声练习关注从高到低的下降。可以配合下摆手势，完整读一次。",
    }
    delta = features["end_minus_start_semitones"]
    movement = "有所上升" if delta > 1 else "有所下降" if delta < -1 else "较接近"
    measured = (f"有效 F0 从约 {features['start_f0_hz']:.0f} Hz 到 "
                f"{features['end_f0_hz']:.0f} Hz，首尾音高{movement}；"
                f"最低有效 F0 约 {features['min_f0_hz']:.0f} Hz。")
    falling = 12 * np.log2(features['start_f0_hz'] / features['min_f0_hz'])
    rising = 12 * np.log2(features['end_f0_hz'] / features['min_f0_hz'])
    if falling > 1 and rising > 1 and 0.1 < features['minimum_position'] < 0.9:
        measured += f"可见先降后升走势，最低点位于首尾有效 F0 时间跨度的约 {features['minimum_position']*100:.0f}% 处。"
    if quality["warnings"]:
        measured += "录音存在质量提示，建议先回听并检查选区。"
    return [measured, descriptions[tone], "固定录音设置再练三次，比较目标曲线和本人两次的升降变化。"]


def analyze(samples: np.ndarray, rate: int, tone: int, floor: float, ceiling: float,
            start: float | None, end: float | None, harness=None, reference=None) -> dict:
    def begin(stage):
        if harness:
            harness.begin(stage)
    def finish(summary):
        if harness:
            harness.end(summary)
    begin('audio.segment')
    duration = len(samples) / rate
    if not math.isfinite(floor) or not math.isfinite(ceiling) or not (50 <= floor < ceiling <= 1000):
        raise AudioError("基频范围需要满足 50 ≤ 下限 < 上限 ≤ 1000 Hz。")
    if (start is None) != (end is None):
        raise AudioError("请同时指定选区起点和终点。")
    automatic = start is None
    if automatic:
        start, end = suggested_bounds(samples, rate)
    if not all(math.isfinite(v) for v in (start, end)) or not (0 <= start < end <= duration + 0.001):
        raise AudioError("选区超出录音范围，请调整起点和终点。")
    if end - start < 0.12:
        raise AudioError("选区至少需要 0.12 秒，请包含一次完整发音。")
    first = int(round(start * rate))
    last = min(len(samples), int(round(end * rate)))
    offset = first / rate
    selected = samples[first:last]
    finish(f'选区 {offset:.3f}–{last / rate:.3f} 秒；' + ('能量建议' if automatic else '手动选择'))
    begin('praat.extract')
    with PRAAT_LOCK:
        pitch = parselmouth.Sound(selected, rate).to_pitch_ac(
            time_step=0.01, pitch_floor=floor, pitch_ceiling=ceiling,
        )
        times = pitch.xs() + offset
        f0 = pitch.selected_array["frequency"].copy()
    finish(f'Praat {parselmouth.PRAAT_VERSION}，10 ms 步长，{len(f0)} 帧')
    begin('audio.quality')
    valid = f0 > 0
    warnings = []
    if np.max(np.abs(selected)) < 0.01:
        warnings.append("音量较低，请回听确认声音清楚。")
    clipping = float(np.mean(np.abs(selected) >= 0.995))
    if clipping > 0.005:
        warnings.append("可能有削波，请稍远离麦克风或降低输入音量。")
    if np.count_nonzero(valid) < 8:
        warnings.append("有效基频帧较少，请重新录音或调整选区。")
    elif float(valid.mean()) < 0.5:
        warnings.append("选区中的基频缺失较多，可能包含停顿、清音或低音区断裂。")
    adjacent = valid[1:] & valid[:-1]
    differences = np.abs(12 * np.log2(np.maximum(f0[1:], 1) / np.maximum(f0[:-1], 1)))
    jumps = int(np.count_nonzero(adjacent & (differences >= 11)))
    if jumps:
        warnings.append("曲线存在较大的相邻跳变，请检查基频范围；原始提取值已保留。")
    finish(f'{int(valid.sum())}/{len(f0)} 帧有有效 F0；{len(warnings)} 条质量提示')
    begin('tone.features')
    features = None
    if np.count_nonzero(valid) >= 8:
        values, voiced_times = f0[valid], times[valid]
        minimum_index = int(np.argmin(values))
        start_f0 = float(np.median(values[:3]))
        end_f0 = float(np.median(values[-3:]))
        span = float(voiced_times[-1] - voiced_times[0])
        features = {
            "selected_duration_s": round((last - first) / rate, 4),
            "first_to_last_f0_s": round(span, 4),
            "mean_f0_hz": round(float(values.mean()), 3),
            "min_f0_hz": round(float(values.min()), 3),
            "max_f0_hz": round(float(values.max()), 3),
            "start_f0_hz": round(start_f0, 3),
            "end_f0_hz": round(end_f0, 3),
            "minimum_time_s": round(float(voiced_times[minimum_index]), 4),
            "minimum_position": round(float((voiced_times[minimum_index] - voiced_times[0]) / span), 4),
            "end_minus_start_semitones": round(float(12 * np.log2(end_f0 / start_f0)), 3),
        }
    finish('已计算观测特征' if features else '有效 F0 不足，特征保留为空')
    quality = {"valid_frames": int(valid.sum()), "total_frames": len(f0),
               "clipped_fraction": round(clipping, 5), "octave_jumps": jumps, "warnings": warnings}
    result = {
        "tone": tone, "duration_s": round(duration, 5), "sample_rate_hz": rate,
        "selection": {"start_s": round(offset, 5), "end_s": round(last / rate, 5), "automatic": automatic},
        "time_s": [round(float(t), 5) for t in times],
        "f0_hz": [round(float(v), 3) if v > 0 else None for v in f0],
        "waveform": waveform(samples), "features": features, "quality": quality,
        "energy_dbfs": [round(20*math.log10(max(float(np.sqrt(np.mean(
            selected[max(0,int((t-offset)*rate)-int(rate*.01)):min(len(selected),int((t-offset)*rate)+int(rate*.01))]**2))),1e-5)),2) for t in times],
        "algorithm": {"method": "raw_autocorrelation", "parselmouth_version": parselmouth.__version__,
                      "praat_version": parselmouth.PRAAT_VERSION, "step_s": 0.01,
                      "pitch_floor_hz": floor, "pitch_ceiling_hz": ceiling},
    }
    begin('tone.score')
    result['score']=evaluate(result,reference)
    finish(f"轮廓分 {result['score']['total']}；已保存参照与计算版本" if result['score']['total'] is not None else result['score']['reason'])
    begin('tone.feedback')
    result['feedback']=teaching_feedback(tone,features,quality)
    finish('已生成曲线解释与复练建议')
    return result
