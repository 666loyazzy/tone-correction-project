import {BrowserRecorder, audioExtension} from './recorder.js';

const $ = selector => document.querySelector(selector);
const tones = ['mā', 'má', 'mǎ', 'mà'];
const recorder = new BrowserRecorder();
const state = {tone: 3, blob: null, url: null, duration: 0, waveform: [],
  cropEdited: false, result: null, busy: false, generation: 0, chatBusy: false,
  user: null, practiceId: null, conversationId: null, reviewOnly: false};
let history = [];
let requestController;
let authMode = 'login';
let authStatus = {needs_setup:false, setup_token_required:false};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/auth/')) showAuth();
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求参数不正确，请检查输入。');
  }
  return data;
}

function jsonOptions(method, body) {
  return {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
}

function showAuth() {
  $('.shell').inert = true;
  if (!$('#auth-dialog').open) $('#auth-dialog').showModal();
  const register = authMode !== 'login';
  $('#auth-title').textContent = authStatus.needs_setup ? '创建教师账号' : register ? '加入声调练习' : '欢迎回到声调纠正系统';
  $('#auth-description').textContent=register&&!authStatus.needs_setup?'使用教师邀请码加入后，该教师可以查看你的练习录音与分析。':'登录后保存录音、练习记录和学习记忆。';
  $('#auth-submit').textContent = register ? '创建账号并开始' : '登录';
  $('#display-field').hidden = !register;
  $('#auth-display').required = register;
  $('#invite-field').hidden = !register || authStatus.needs_setup;
  $('#auth-invite').required = register && !authStatus.needs_setup;
  $('#setup-field').hidden = !authStatus.needs_setup || !authStatus.setup_token_required;
  $('#auth-setup').required = authStatus.needs_setup && authStatus.setup_token_required;
  $('#auth-switch').hidden = authStatus.needs_setup;
  $('#auth-switch').textContent = register ? '已有账号？返回登录' : '有邀请码？创建学习者账号';
}

async function loadHistory() {
  const data = await api('/api/practices');
  history = data.practices.map(item => ({...item, created:new Date(item.created_at*1000).toISOString()}));
  renderHistory();
}

async function loadConversations() {
  const data = await api('/api/conversations');
  const select = $('#conversation-select'); select.replaceChildren();
  const empty = document.createElement('option'); empty.value='';empty.textContent='当前新对话'; select.append(empty);
  for (const item of data.conversations) {
    const option = document.createElement('option');option.value=item.id;option.textContent=item.title;select.append(option);
  }
  select.value=state.conversationId || '';
  $('#delete-chat').hidden=!state.conversationId;
}

async function loggedIn(user) {
  state.user=user;
  $('#auth-dialog').close();$('.shell').inert=false;
  $('#user-name').textContent=user.display_name;
  $('#teacher-nav').hidden=user.role!=='teacher';
  const config=await api('/api/agent/config');
  $('#chat-mode').textContent=config.model_configured?'模型问答':'本地教学规则';
  await loadHistory();await loadConversations();
}

function notify(message, error = false) {
  $('#record-status').textContent = message;
  $('#record-status').classList.toggle('error', error);
}

function updateButtons() {
  const captureBusy = ['requesting', 'recording', 'stopping'].includes(recorder.state);
  $('#record').disabled = state.busy || recorder.state === 'stopping';
  $('#record').classList.toggle('recording', recorder.state === 'recording');
  $('#record-text').textContent = ({requesting:'取消等待', recording:'停止录音', stopping:'正在整理…'})[recorder.state] || (state.blob ? '重新录音' : '开始录音');
  $('#recording-stage').classList.toggle('recording', recorder.state === 'recording');
  $('#recording-label').textContent = ({requesting:'等待麦克风授权', recording:'正在录音', stopping:'整理音频中'})[recorder.state] || (state.blob ? '录音已就绪' : '准备好，就开始');
  $('#upload').disabled = captureBusy || state.busy;
  $('#analyze').disabled = !state.blob || state.busy || captureBusy;
  $('#analyze').textContent = state.busy ? '正在提取声调…' : '分析这次发音 ↗';
  $('#export').disabled = !state.result;
  $('#delete-practice').hidden = !state.practiceId || state.reviewOnly;
  $('#delete-practice').disabled = state.busy || captureBusy;
  for (const element of document.querySelectorAll('.tone, #start, #end, #floor, #ceiling, .history-item')) {
    element.disabled = captureBusy || state.busy;
  }
  $('#download').hidden = captureBusy;
  $('#audio').hidden = captureBusy;
}

function chooseTone(tone) {
  state.tone = tone;
  document.querySelectorAll('.tone').forEach(button => {
    const selected = Number(button.dataset.tone) === tone;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  $('#exercise-note').textContent = `以舒适音量完整读一次“${tones[tone - 1]}”。` + (tone === 3 ? '这里练习的是孤立三声。' : '一次录一个音节，读完后点击停止。');
}

function addMessage(text, role = 'assistant', sources = []) {
  const message = document.createElement('div');
  message.className = `message ${role}`;
  if (role === 'assistant') {
    const avatar = document.createElement('span');
    avatar.className = 'avatar'; avatar.textContent = '声'; message.append(avatar);
  }
  const content = document.createElement('div');
  for (const paragraph of (Array.isArray(text) ? text : String(text).split('\n')).filter(Boolean)) {
    const p = document.createElement('p'); p.textContent = paragraph; content.append(p);
  }
  if (sources.length) {
    const references=document.createElement('div'); references.className='message-sources'; references.textContent='参考资料';
    sources.forEach(source => {
      const link=document.createElement(source.url?'a':'span');link.textContent=source.title;
      if (source.url) {link.href=source.url;link.target='_blank';link.rel='noopener noreferrer';}
      references.append(link);
    });
    content.append(references);
  }
  message.append(content); $('#messages').append(message);
  while ($('#messages').children.length > 60) $('#messages').firstElementChild.remove();
  $('#messages').scrollTop = $('#messages').scrollHeight;
}

function renderWaveform() {
  if (!state.waveform.length) { $('#waveform').replaceChildren(); return; }
  const width = 600, height = 60;
  const lines = state.waveform.map(([low, high], index) => {
    const x = index / state.waveform.length * width;
    return `M${x.toFixed(2)},${(height/2-low*height*.46).toFixed(2)}L${x.toFixed(2)},${(height/2-high*height*.46).toFixed(2)}`;
  }).join(' ');
  let selection = '';
  if (state.duration) {
    const start = Math.max(0, Math.min(state.duration, Number($('#start').value) || 0));
    const end = Math.max(start, Math.min(state.duration, Number($('#end').value) || state.duration));
    selection = `<rect x="${start/state.duration*width}" y="0" width="${(end-start)/state.duration*width}" height="${height}" fill="#dce7ca" opacity=".45"/>`;
  }
  $('#waveform').innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${selection}<path class="wave-path" d="${lines}"/></svg>`;
}

async function setAudio(blob, filename, elapsed = 0) {
  const generation = ++state.generation;
  if (state.url) URL.revokeObjectURL(state.url);
  state.blob = blob;
  state.url = URL.createObjectURL(blob);
  state.duration = elapsed;
  state.waveform = [];
  state.result = null;
  state.practiceId = null;
  state.reviewOnly = false;
  $('#analysis-flow').hidden = true;
  state.cropEdited = false;
  $('#audio').src = state.url;
  $('#download').href = state.url;
  $('#download').download = filename;
  $('#preview').hidden = false;
  $('#start').value = '0';
  $('#end').value = elapsed ? elapsed.toFixed(2) : '0';
  $('#quality').hidden = true;
  renderChart(); updateButtons(); renderWaveform();
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return;
  let context;
  try {
    context = new Context();
    const decoded = await context.decodeAudioData(await blob.arrayBuffer());
    if (generation !== state.generation) return;
    state.duration = decoded.duration;
    $('#end').value = decoded.duration.toFixed(2);
    const data = decoded.getChannelData(0);
    const bins = 320, size = Math.ceil(data.length / bins);
    for (let i = 0; i < data.length; i += size) {
      let low = 0, high = 0;
      for (let j = i; j < Math.min(data.length, i + size); j++) {
        low = Math.min(low, data[j]); high = Math.max(high, data[j]);
      }
      state.waveform.push([low, high]);
    }
    renderWaveform();
  } catch {
    if (generation === state.generation) notify('录音已就绪。当前浏览器无法预览波形，可回听后交给服务器分析。');
  } finally { if (context) context.close().catch(() => {}); }
}

function relativePoints(result) {
  const {start_s, end_s} = result.selection;
  return result.time_s.map((time, index) => [(time - start_s)/(end_s-start_s), result.f0_hz[index]]);
}

function renderChart() {
  const result = state.result;
  if (!result) {
    $('#chart').innerHTML = '<div class="empty-chart"><svg viewBox="0 0 240 80" aria-hidden="true"><path d="M5 12 C45 22 56 70 110 68 S180 30 235 8"/></svg><b>你的声音，会在这里留下曲线</b><p>完成一次录音并分析，观察音高如何随时间变化。</p></div>';
    $('#metrics').innerHTML = '<div><small>最低音高</small><strong>— <em>Hz</em></strong></div><div><small>结束音高</small><strong>— <em>Hz</em></strong></div><div><small>分析选区</small><strong>— <em>秒</em></strong></div>';
    return;
  }
  const previous = $('#compare').checked ? history.find(item => item.result?.tone === result.tone && item.id !== state.practiceId)?.result : null;
  const points = relativePoints(result);
  const previousPoints = previous ? relativePoints(previous) : [];
  const values = [...points, ...previousPoints].filter(point => Number.isFinite(point[1])).map(point => point[1]);
  const minimum = values.length ? Math.max(0, Math.floor((Math.min(...values)-15)/25)*25) : 50;
  const maximum = values.length ? Math.max(minimum+75, Math.ceil((Math.max(...values)+15)/25)*25) : 350;
  const width=560, height=235, left=43, right=16, top=19, bottom=38;
  const x = value => left + value*(width-left-right);
  const y = value => top + (maximum-value)/(maximum-minimum)*(height-top-bottom);
  let svg = '';
  for (let i=0; i<=4; i++) {
    const value=minimum+(maximum-minimum)*i/4;
    svg += `<line class="chart-grid" x1="${left}" y1="${y(value)}" x2="${width-right}" y2="${y(value)}"/><text class="chart-label" x="${left-9}" y="${y(value)+3}" text-anchor="end">${Math.round(value)}</text>`;
    const tick = previous ? `${i*25}%` : `${((result.selection.end_s-result.selection.start_s)*i/4).toFixed(2)}`;
    svg += `<text class="chart-label" x="${x(i/4)}" y="${height-17}" text-anchor="middle">${tick}</text>`;
  }
  function path(data) {
    let d='', connected=false;
    for (const [time,f0] of data) {
      if (!Number.isFinite(f0)) {connected=false; continue;}
      d += `${connected?'L':'M'}${x(time).toFixed(2)},${y(f0).toFixed(2)} `; connected=true;
    }
    return d;
  }
  if (previous) svg += `<path class="previous-line" d="${path(previousPoints)}"/>`;
  svg += `<path class="f0-line" d="${path(points)}"/>`;
  if (result.features) {
    const index = result.f0_hz.indexOf(result.features.min_f0_hz);
    if (index >= 0) svg += `<circle class="chart-dot" cx="${x(points[index][0])}" cy="${y(points[index][1])}" r="4"/>`;
  } else svg += `<text class="chart-label" x="${width/2}" y="${height/2}" text-anchor="middle">有效基频不足，请回听或重新录音</text>`;
  svg += `<text class="chart-label" x="${width/2}" y="${height-1}" text-anchor="middle">${previous?'选区内归一化时间 · 虚线为上次练习':'选区内相对时间 · 秒'}</text>`;
  $('#chart').innerHTML = `<svg role="img" aria-label="声调基频随时间变化的曲线" viewBox="0 0 ${width} ${height}">${svg}</svg>`;
  const f = result.features;
  const duration = result.selection.end_s-result.selection.start_s;
  $('#metrics').innerHTML = `<div><small>最低音高</small><strong>${f?Math.round(f.min_f0_hz):'—'} <em>Hz</em></strong></div><div><small>结束音高</small><strong>${f?Math.round(f.end_f0_hz):'—'} <em>Hz</em></strong></div><div><small>分析选区</small><strong>${duration.toFixed(2)} <em>秒</em></strong></div>`;
}

function renderHistory() {
  $('#history-count').textContent = String(history.length);
  $('#history').replaceChildren();
  if (!history.length) {
    const empty=document.createElement('p'); empty.className='empty-history';
    empty.textContent='完成分析后，你的练习会出现在这里。'; $('#history').append(empty); return;
  }
  history.forEach(item => {
    const button=document.createElement('button'); button.className='history-item'; button.type='button';
    const text=document.createElement('span'); text.textContent=`${tones[item.tone-1]} · ${item.state==='completed'?'已完成':item.state==='failed'?'需重录':'分析中'}`;
    const time=document.createElement('small'); time.textContent=new Date(item.created).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
    button.append(text,time); $('#history').append(button);
    button.addEventListener('click', () => openPractice(item));
  });
  updateButtons();
}

function showTrace(practice) {
  $('#analysis-flow').hidden=false;
  $('#flow-status').textContent=({queued:'等待分析',running:'分析中',completed:'已完成',failed:'需要重新录音'})[practice.state] || '';
  $('#trace').replaceChildren();
  (practice.trace||[]).forEach(stage=>{
    const li=document.createElement('li');li.className=stage.status;
    const icon=document.createElement('i');icon.textContent=stage.status==='completed'?'✓':stage.status==='running'?'…':stage.status==='failed'?'!':'·';
    const label=document.createElement('span');label.textContent=stage.label;
    li.append(icon,label);if(stage.summary)li.title=stage.summary;$('#trace').append(li);
  });
}

async function waitForPractice(id, signal) {
  const deadline=Date.now()+180000;
  while (Date.now()<deadline) {
    if (signal?.aborted) throw new DOMException('Cancelled','AbortError');
    const practice=await api('/api/practices/'+id,{signal});showTrace(practice);
    if (practice.state==='completed') return practice;
    if (practice.state==='failed') throw new Error(practice.error || '分析未完成，请重新录音。');
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  throw new Error('分析仍在进行，可以稍后在练习记录中查看结果。');
}

async function openPractice(item, reviewOnly=false) {
  if (state.busy) return;
  state.busy=true;updateButtons();
  try {
    const row=item.state==='completed'?item:await waitForPractice(item.id);
    if (!row.result) throw new Error(row.error || '这条练习没有分析结果。');
    chooseTone(row.tone);
    if (row.audio_available) {
      const response=await fetch('/api/practices/'+row.id+'/audio');
      if (!response.ok) throw new Error('无法读取录音，请重新登录后再试。');
      await setAudio(await response.blob(),`声调纠正系统-${tones[row.tone-1]}.wav`,row.result.duration_s);
      $('#start').value=row.result.selection.start_s.toFixed(2);$('#end').value=row.result.selection.end_s.toFixed(2);
      state.cropEdited=true;
    } else {
      if (state.url) URL.revokeObjectURL(state.url);
      state.blob=null;state.url=null;$('#audio').removeAttribute('src');$('#preview').hidden=true;
    }
    state.result=row.result;state.practiceId=row.id;state.reviewOnly=reviewOnly;
    renderChart();renderWaveform();showQuality(row.result);showTrace(row);
    addMessage(row.result.feedback);
    notify(row.audio_available?'已载入这次练习的录音和曲线，可回听或复练。':'已载入曲线；音频已过保留期。');
  } catch(error) {notify(error.message,true);}
  finally {state.busy=false;updateButtons();}
}

function showQuality(result) {
  $('#quality').replaceChildren();
  for (const warning of result.quality.warnings) {
    const p=document.createElement('p'); p.textContent=warning; $('#quality').append(p);
  }
  $('#quality').hidden = !result.quality.warnings.length;
}

recorder.addEventListener('statechange', event => {
  updateButtons(); notify(event.detail.message, event.detail.state === 'error');
  if (event.detail.state === 'requesting') $('#audio').pause();
  if (event.detail.state !== 'recording') document.querySelectorAll('.level-bars i').forEach(bar => { bar.style.height='5px'; });
});
recorder.addEventListener('progress', event => {
  const {elapsed,level}=event.detail;
  $('#clock').textContent=`00:${elapsed.toFixed(1).padStart(4,'0')}`;
  document.querySelectorAll('.level-bars i').forEach((bar,index) => {
    bar.style.height=`${Math.min(30,5+level*260*(.4+Math.abs(Math.sin(index*.9))))}px`;
  });
});
recorder.addEventListener('recorded', event => {
  const {blob,type,elapsed_s}=event.detail;
  setAudio(blob, `声调纠正系统-${tones[state.tone-1]}-${Date.now()}.${audioExtension(type)}`, elapsed_s);
});
$('#record').addEventListener('click', () => {
  if (recorder.state==='recording') recorder.stop();
  else if (recorder.state==='requesting') recorder.cancelPermission();
  else { $('#audio').pause(); recorder.start(); }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) recorder.interrupt(); });
window.addEventListener('pagehide', () => { recorder.dispose(); requestController?.abort(); if (state.url) URL.revokeObjectURL(state.url); });
window.addEventListener('pageshow', event => {
  if (event.persisted) location.reload();
});
document.querySelectorAll('.tone').forEach(button => button.addEventListener('click', () => {
  chooseTone(Number(button.dataset.tone)); state.result=null; renderChart(); $('#quality').hidden=true; updateButtons();
}));
$('#upload').addEventListener('click', () => $('#file').click());
$('#file').addEventListener('change', () => {
  const file=$('#file').files[0]; $('#file').value='';
  if (!file) return;
  if (file.size > 12*1024*1024) {notify('文件超过 12 MB，请选择较短的录音。',true); return;}
  setAudio(file,file.name); notify('音频已载入。先回听，再点击分析。');
});
$('#help').addEventListener('click', () => { $('#help-text').hidden=!$('#help-text').hidden; });
$('#compare').addEventListener('change', renderChart);
for (const id of ['start','end']) $('#'+id).addEventListener('input', () => {state.cropEdited=true;renderWaveform();});
$('#analyze').addEventListener('click', async () => {
  if (!state.blob || state.busy) return;
  const form=new FormData();
  form.append('file',state.blob,'recording.'+audioExtension(state.blob.type));
  form.append('tone',String(state.tone));
  form.append('pitch_floor',$('#floor').value); form.append('pitch_ceiling',$('#ceiling').value);
  if (state.cropEdited) {
    const start=Number($('#start').value), end=Number($('#end').value);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end-start<.12 || start<0) {notify('请设置至少 0.12 秒的有效选区。',true);return;}
    form.append('start_s',String(start));form.append('end_s',String(end));
  }
  state.busy=true;updateButtons();notify('正在读取录音并提取声调，请稍候。');
  requestController=new AbortController();
  const timeout=setTimeout(()=>requestController?.abort(),180000);
  try {
    const queued=await api('/api/analyses',{method:'POST',body:form,signal:requestController.signal});
    const practice=await waitForPractice(queued.id,requestController.signal);
    const data=practice.result;
    state.result=data;state.practiceId=practice.id;state.reviewOnly=false;state.duration=data.duration_s;state.waveform=data.waveform;state.cropEdited=false;
    $('#start').value=data.selection.start_s.toFixed(2);$('#end').value=data.selection.end_s.toFixed(2);
    await loadHistory();renderChart();renderWaveform();showQuality(data);addMessage(data.feedback);
    notify('分析已完成。可以调整选区重新分析，或再录一次对比走势。');
  } catch(error) {
    notify(error.name==='AbortError'?'分析等待超时，录音仍保留，可再次提交。':(error.message||'分析未完成，请重试。'),true);
  } finally {clearTimeout(timeout);requestController=null;state.busy=false;updateButtons();}
});
$('#export').addEventListener('click', () => {
  if (!state.result) return;
  const csv=['Time_s,F0_Hz',...state.result.time_s.map((time,index)=>`${time},${state.result.f0_hz[index]??''}`)].join('\n');
  const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));
  const a=document.createElement('a');a.href=url;a.download=`声调纠正系统-${tones[state.result.tone-1]}-F0.csv`;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('#chat-form').addEventListener('submit',async event=>{
  event.preventDefault();const message=$('#chat-input').value.trim();
  if (!message || state.chatBusy) return;
  addMessage(message,'user');$('#chat-input').value='';state.chatBusy=true;$('#chat-send').disabled=true;
  try {
    $('#chat-flow').hidden=false;$('#chat-flow').textContent='声调纠正系统正在结合学习档案与当前练习整理建议…';
    const data=await api('/api/chat',jsonOptions('POST',{message,tone:state.tone,conversation_id:state.conversationId,analysis_id:state.reviewOnly?null:state.practiceId}));
    state.conversationId=data.conversation_id;addMessage(data.text,'assistant',data.sources);
    $('#chat-flow').textContent=data.trace.map(step=>step.label).join(' → ');
    $('#chat-mode').textContent=data.mode==='api'?'模型问答':data.mode==='guarded'?'已校验的教学反馈':'本地教学规则';
    await loadConversations();
  } catch(error) {addMessage(error.message||'网络暂时不可用。你可以继续录音和分析。');}
  finally {state.chatBusy=false;$('#chat-send').disabled=false;}
});

$('#delete-practice').addEventListener('click',async()=>{
  if(!state.practiceId || state.reviewOnly)return;
  try {
    await api('/api/practices/'+state.practiceId,{method:'DELETE'});
    state.practiceId=null;state.result=null;$('#analysis-flow').hidden=true;$('#quality').hidden=true;
    if(state.url)URL.revokeObjectURL(state.url);state.blob=null;state.url=null;$('#preview').hidden=true;
    await loadHistory();renderChart();updateButtons();notify('这次练习、录音及对应学习摘要已删除。');
  }catch(error){notify(error.message,true);}
});
$('#auth-dialog').addEventListener('cancel',event=>event.preventDefault());
$('#auth-switch').addEventListener('click',()=>{authMode=authMode==='login'?'register':'login';$('#auth-error').textContent='';showAuth();});
$('#auth-form').addEventListener('submit',async event=>{
  event.preventDefault();$('#auth-submit').disabled=true;$('#auth-error').textContent='';
  const body={username:$('#auth-username').value.trim(),password:$('#auth-password').value};
  if(authMode!=='login')Object.assign(body,{display_name:$('#auth-display').value.trim(),invite_code:$('#auth-invite').value.trim(),setup_token:$('#auth-setup').value.trim()});
  try {
    const data=await api('/api/auth/'+(authMode==='login'?'login':'register'),jsonOptions('POST',body));
    $('#auth-password').value='';$('#auth-setup').value='';await loggedIn(data.user);
  }catch(error){$('#auth-error').textContent=error.message;}
  finally{$('#auth-submit').disabled=false;}
});
async function logout(){try{await api('/api/auth/logout',{method:'POST'});location.reload();}catch(error){notify(error.message,true);}}
$('#logout').addEventListener('click',logout);$('#mobile-logout').addEventListener('click',logout);
document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$('#'+button.dataset.close).close()));
async function openMemory(){
  try{
    const memory=await api('/api/memory');$('#memory-goal').value=memory.goal;$('#memory-enabled').checked=memory.enabled;
    $('#memory-facts').textContent=`已保存 ${memory.facts.length} 次练习摘要。内容包含观测数据和来源，可随时清空。`;
    $('#memory-status').textContent='';$('#memory-dialog').showModal();
  }catch(error){notify(error.message,true);}
}
$('#memory-nav').addEventListener('click',openMemory);$('#mobile-memory').addEventListener('click',openMemory);
$('#memory-form').addEventListener('submit',async event=>{
  event.preventDefault();try{await api('/api/memory',jsonOptions('PUT',{enabled:$('#memory-enabled').checked,goal:$('#memory-goal').value.trim()}));$('#memory-status').textContent='学习档案已保存。';}catch(error){$('#memory-status').textContent=error.message;}
});
$('#clear-memory').addEventListener('click',async()=>{
  try{await api('/api/memory',{method:'DELETE'});$('#memory-facts').textContent='练习摘要已清空。练习记录保留，可单独删除。';}catch(error){$('#memory-status').textContent=error.message;}
});
const welcomeHTML=$('#messages').innerHTML;
$('#new-chat').addEventListener('click',()=>{if(state.chatBusy)return;state.conversationId=null;$('#messages').innerHTML=welcomeHTML;$('#chat-flow').hidden=true;loadConversations();});
$('#conversation-select').addEventListener('change',async()=>{
  if(state.chatBusy)return;
  const id=$('#conversation-select').value;
  if(!id){state.conversationId=null;$('#messages').innerHTML=welcomeHTML;$('#delete-chat').hidden=true;return;}
  try{const data=await api('/api/conversations/'+id);state.conversationId=id;$('#messages').replaceChildren();data.messages.forEach(message=>addMessage(message.content,message.role,message.sources));$('#delete-chat').hidden=false;}
  catch(error){addMessage(error.message);}
});
$('#delete-chat').addEventListener('click',async()=>{
  if(!state.conversationId || state.chatBusy)return;
  try{await api('/api/conversations/'+state.conversationId,{method:'DELETE'});state.conversationId=null;$('#messages').innerHTML=welcomeHTML;await loadConversations();}catch(error){addMessage(error.message);}
});
$('#teacher-nav').addEventListener('click',async()=>{
  try{
    const data=await api('/api/teacher/students');$('#student-list').replaceChildren();
    if(!data.students.length)$('#student-list').textContent='暂时没有学习者，可以先生成邀请码。';
    for(const student of data.students){
      const button=document.createElement('button');button.type='button';button.textContent=`${student.display_name} · 查看练习 ↗`;$('#student-list').append(button);
      button.addEventListener('click',async()=>{
        try{const data=await api(`/api/teacher/students/${student.id}/practices`);const completed=data.practices.filter(row=>row.state==='completed');
          $('#student-list').replaceChildren();if(!completed.length)$('#student-list').textContent='这位学习者还没有已完成的练习。';
          for(const row of completed){const entry=document.createElement('button');entry.type='button';entry.textContent=`${student.display_name} · ${tones[row.tone-1]} · ${new Date(row.created_at*1000).toLocaleString('zh-CN')}`;$('#student-list').append(entry);entry.addEventListener('click',()=>{$('#teacher-dialog').close();openPractice(row,true);});}
        }catch(error){$('#invite-result').textContent=error.message;}
      });
    }
    $('#teacher-dialog').showModal();
  }catch(error){notify(error.message,true);}
});
$('#create-invite').addEventListener('click',async()=>{
  try{const data=await api('/api/auth/invites',{method:'POST'});$('#invite-result').textContent=`邀请码：${data.invite_code}（7 天内有效，可使用一次）。学习者在登录页选择创建账号。`;}
  catch(error){$('#invite-result').textContent=error.message;}
});

async function initialize(){
  $('.shell').inert=true;updateButtons();
  try{
    authStatus=await api('/api/auth/status');authMode=authStatus.needs_setup?'register':'login';
    const response=await fetch('/api/auth/me');
    if(response.ok)await loggedIn((await response.json()).user);else showAuth();
  }catch(error){showAuth();$('#auth-error').textContent='暂时无法连接服务，请检查服务器后刷新。';}
}
initialize();
