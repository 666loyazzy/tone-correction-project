export const MIME_TYPES = [
  'audio/webm;codecs=opus', 'audio/mp4;codecs=mp4a.40.2',
  'audio/mp4', 'audio/ogg;codecs=opus', 'audio/webm',
];

const PERMISSION_ERRORS = {
  NotAllowedError: '麦克风未获授权。请在浏览器的网站权限中允许麦克风，或上传已有录音。',
  NotFoundError: '没有找到麦克风。请连接设备，或上传已有录音。',
  NotReadableError: '麦克风暂时无法使用。请检查其他应用是否占用麦克风。',
  OverconstrainedError: '当前麦克风无法满足录音设置，请更换设备或上传音频。',
};

export function audioExtension(type = '') {
  if (type.includes('mp4') || type.includes('aac')) return 'm4a';
  if (type.includes('ogg')) return 'ogg';
  if (type.includes('wav')) return 'wav';
  if (type.includes('mpeg')) return 'mp3';
  return type.includes('webm') ? 'webm' : 'audio';
}

export class BrowserRecorder extends EventTarget {
  constructor({maxSeconds = 15, permissionTimeout = 20000, platform = globalThis} = {}) {
    super();
    this.platform = platform;
    this.maxSeconds = maxSeconds;
    this.permissionTimeout = permissionTimeout;
    this.state = 'idle';
    this.message = '';
    this._ticket = 0;
    this._stream = null;
    this._context = null;
    this._recorder = null;
    this._permissionTimer = null;
    this._timer = null;
    this._started = 0;
    this._stopPromise = null;
    this._resolveStop = null;
    this._notice = '';
    this._disposed = false;
  }

  _emit(type, detail) { this.dispatchEvent(new CustomEvent(type, {detail})); }
  _set(state, message = '') {
    this.state = state;
    this.message = message;
    this._emit('statechange', {state, message});
  }

  async start() {
    if (this._disposed || ['requesting', 'recording', 'stopping'].includes(this.state)) return;
    const p = this.platform;
    if (!p.isSecureContext || !p.navigator?.mediaDevices?.getUserMedia) {
      this._set('error', '录音需要 HTTPS 或 localhost。当前页面可以上传已有音频。');
      return;
    }
    if (!p.MediaRecorder) {
      this._set('error', '当前浏览器不支持网页录音，请使用新版浏览器或上传已有录音。');
      return;
    }
    const ticket = ++this._ticket;
    this._notice = '';
    this._stopPromise = null;
    this._set('requesting', '请在浏览器弹窗中允许使用麦克风。');
    const Context = p.AudioContext || p.webkitAudioContext;
    if (Context) {
      try {
        this._context = new Context();
        this._context.resume().catch(() => {});
      } catch { this._context = null; }
    }
    this._permissionTimer = setTimeout(() => {
      if (ticket !== this._ticket) return;
      this.cancelPermission();
      this._set('error', '尚未获得麦克风授权。请检查浏览器弹窗，再点击开始录音。');
    }, this.permissionTimeout);
    let stream;
    try {
      stream = await p.navigator.mediaDevices.getUserMedia({
        video: false,
        audio: {channelCount: {ideal: 1}, echoCancellation: {ideal: false},
          noiseSuppression: {ideal: false}, autoGainControl: {ideal: false}},
      });
      // Permission can resolve after cancellation. Release this late stream immediately.
      if (ticket !== this._ticket || this._disposed) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      clearTimeout(this._permissionTimer);
      this._permissionTimer = null;
      this._stream = stream;
      const mimeType = MIME_TYPES.find(type => p.MediaRecorder.isTypeSupported(type));
      try {
        this._recorder = new p.MediaRecorder(stream, mimeType ? {mimeType} : {});
      } catch {
        this._recorder = new p.MediaRecorder(stream);
      }
      const recorder = this._recorder;
      const chunks = [];
      const settings = stream.getAudioTracks()[0]?.getSettings?.() || {};
      let failed = false;
      recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
      recorder.onerror = () => {
        failed = true;
        this._notice = '录音被设备中断，请重新录制或上传已有录音。';
        if (recorder.state !== 'inactive') this.stop();
        else { this._cleanup(); this._set('error', this._notice); }
      };
      recorder.onstop = () => {
        const duration = (performance.now() - this._started) / 1000;
        const type = recorder.mimeType || chunks.find(chunk => chunk.type)?.type || '';
        const blob = new Blob(chunks, {type});
        this._cleanup();
        this._recorder = null;
        if (failed || !blob.size || duration < 0.15) {
          this._set('error', this._notice || '录音过短或没有生成音频，请重新录音。');
          this._resolveStop?.(null);
        } else {
          const result = {blob, type, elapsed_s: duration, settings: {
            sampleRate: settings.sampleRate, channelCount: settings.channelCount,
            echoCancellation: settings.echoCancellation, noiseSuppression: settings.noiseSuppression,
            autoGainControl: settings.autoGainControl,
          }};
          this._set('ready', this._notice || '录音已完成。先回听，再提交分析。');
          this._emit('recorded', result);
          this._resolveStop?.(result);
        }
        this._resolveStop = null;
      };
      stream.getAudioTracks().forEach(track => {
        track.onended = () => {
          if (this.state === 'recording') {
            this._notice = '麦克风连接中断，已保留本次录音。请回听确认是否完整。';
            this.stop();
          }
        };
      });
      recorder.start(250);
      this._started = performance.now();
      this._set('recording', '以舒适音量读一次，读完后点击停止。');
      let analyser;
      let data;
      try {
        if (this._context) {
          analyser = this._context.createAnalyser();
          analyser.fftSize = 1024;
          this._context.createMediaStreamSource(stream).connect(analyser);
          data = new Float32Array(analyser.fftSize);
        }
      } catch { analyser = null; }
      this._timer = setInterval(() => {
        const elapsed = (performance.now() - this._started) / 1000;
        let level = 0;
        if (analyser) {
          analyser.getFloatTimeDomainData(data);
          level = Math.sqrt(data.reduce((sum, value) => sum + value * value, 0) / data.length);
        }
        this._emit('progress', {elapsed, level});
        if (elapsed >= this.maxSeconds) {
          this._notice = `已达到 ${this.maxSeconds} 秒，录音自动停止。请回听确认。`;
          this.stop();
        }
      }, 100);
    } catch (error) {
      if (ticket !== this._ticket) return;
      this._cleanup();
      this._set('error', PERMISSION_ERRORS[error.name] || '录音启动失败，请重试或上传已有音频。');
    }
  }

  stop() {
    if (this._stopPromise) return this._stopPromise;
    if (!this._recorder || this._recorder.state === 'inactive') return Promise.resolve(null);
    this._stopPromise = new Promise(resolve => { this._resolveStop = resolve; });
    this._set('stopping', '正在整理录音，请稍候。');
    clearInterval(this._timer);
    this._timer = null;
    this._recorder.stop();
    return this._stopPromise;
  }

  cancelPermission() {
    if (this.state !== 'requesting') return;
    ++this._ticket;
    this._cleanup();
    this._set('idle', '已取消授权等待，可以重新开始或上传音频。');
  }

  interrupt() {
    if (this.state !== 'recording') return;
    this._notice = '页面进入后台，已停止录音。请回听确认是否完整。';
    this.stop();
  }

  _cleanup() {
    clearTimeout(this._permissionTimer);
    clearInterval(this._timer);
    this._permissionTimer = this._timer = null;
    this._stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    this._stream = null;
    if (this._context) this._context.close().catch(() => {});
    this._context = null;
  }

  dispose() {
    this._disposed = true;
    ++this._ticket;
    if (this._recorder) {
      this._recorder.onstop = null;
      this._recorder.ondataavailable = null;
      if (this._recorder.state !== 'inactive') this._recorder.stop();
    }
    this._resolveStop?.(null);
    this._cleanup();
  }
}
