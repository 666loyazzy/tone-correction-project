const $ = selector => document.querySelector(selector);
const toneNames = ['一声 · 高平', '二声 · 上升', '三声 · 降升', '四声 · 下降'];
let current = null;
let lastAutoPractice = null;

function openVideo() {
  if (!current) return;
  const dialog = $('#principle-video-dialog'), video = $('#principle-video');
  $('#principle-video-title').textContent = toneNames[current.tone - 1] + ' · 发音医学原理';
  $('#principle-video-description').textContent = `本次声调轮廓分 ${current.score.toFixed(1)} 分，查看对应声调的发音原理视频。`;
  video.poster = `/assets/media/tone-principle-${current.tone}.jpg`;
  video.src = `/assets/media/tone-principle-${current.tone}.mp4`;
  video.load();
  document.querySelectorAll('audio').forEach(audio => audio.pause());
  if (!dialog.open) dialog.showModal();
}

export function renderPrincipleVideo(result, {autoOpen = false, practiceId = null} = {}) {
  const tone = result?.tone, score = result?.score?.total;
  const valid = Number.isInteger(tone) && tone >= 1 && tone <= 4 &&
    Number.isFinite(score) && score >= 0 && score <= 100;
  if (!valid) {
    current = null;
    $('#principle-video-entry').hidden = true;
    if ($('#principle-video-dialog').open) $('#principle-video-dialog').close();
    return;
  }
  current = {tone, score};
  $('#principle-video-caption').textContent = `${toneNames[tone - 1]} · 本次声调轮廓分 ${score.toFixed(1)} 分`;
  $('#principle-video-entry').hidden = false;
  if (autoOpen && score < 80 && practiceId && lastAutoPractice !== practiceId) {
    lastAutoPractice = practiceId;
    openVideo();
  }
}

$('#view-principle-video').addEventListener('click', openVideo);
$('#principle-video-dialog').addEventListener('close', () => {
  const video = $('#principle-video');
  video.pause();
  video.removeAttribute('src');
  video.load();
});
$('#principle-video').addEventListener('error', () => {
  $('#principle-video-description').textContent = '视频暂时未能加载，请关闭窗口后点击入口重试。';
});
window.addEventListener('pagehide', () => $('#principle-video').pause());
