---
name: praat-f0
version: 1.0.0
---

使用服务器固定的 Praat-Parselmouth 算法。模型不得生成或执行任意分析脚本。

- 使用 10 ms 步长与配置的基频上下限。
- 时间来自 Praat 实际帧时间，回加选区偏移。
- 记录 Praat、Parselmouth 版本与参数。
- 无有效 F0 的帧返回 null，不填 0、不跨缺失区间插值。
- 原始基频保留；跳变标记不自动改写音高。
