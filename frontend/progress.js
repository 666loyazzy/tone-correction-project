const $=selector=>document.querySelector(selector);
const signed=value=>`${value>0?'+':''}${value.toFixed(1)}`;
let thinkingTimer,thinkingStarted;

export function startThinking(model){
  clearInterval(thinkingTimer);thinkingStarted=Date.now();$('#thinking').hidden=false;
  $('#thinking-title').textContent=model?'DeepSeek 正在分析你的发音':'正在分析声学证据';
  $('#thinking-stage').textContent='连接分析服务';
  const tick=()=>$('#thinking-time').textContent=`已用 ${Math.floor((Date.now()-thinkingStarted)/1000)} 秒`;
  tick();thinkingTimer=setInterval(tick,1000);
}
export function thinkingStage(trace){
  const stage=trace.find(item=>item.status==='running');
  if(stage)$('#thinking-stage').textContent=stage.label;
}
export function stopThinking(){clearInterval(thinkingTimer);$('#thinking').hidden=true;}

function path(times,values,x,y){
  let d='',connected=false;
  values.forEach((value,index)=>{if(!Number.isFinite(value)){connected=false;return;}
    d+=`${connected?'L':'M'}${x(times[index]).toFixed(2)},${y(value).toFixed(2)} `;connected=true;});return d;
}
function plot({minimum=0,maximum=1,label,draw,ticks=true}){
  const x=t=>48+t*472,y=value=>185-(value-minimum)/(maximum-minimum)*155;
  let body='';
  for(let i=0;i<=4;i++){
    const value=minimum+(maximum-minimum)*i/4;
    body+=`<line class="viz-grid" x1="48" y1="${y(value)}" x2="520" y2="${y(value)}"/><text class="viz-label" x="40" y="${y(value)+4}" text-anchor="end">${Number(value.toFixed(1))}</text>`;
    if(ticks)body+=`<text class="viz-label" x="${x(i/4)}" y="207" text-anchor="middle">${i*25}%</text>`;
  }
  return `<svg viewBox="0 0 560 235" role="img" aria-label="${label}">${body}${draw(x,y)}<text class="viz-label" x="280" y="229" text-anchor="middle">${label}</text></svg>`;
}

export function renderDashboard(result,records,tone,practiceId){
  $('#score-dashboard').hidden=!result;
  const score=result?.score;
  if(!score)return;
  const current=records.find(item=>item.id===practiceId);
  const eligible=records.filter(item=>item.comparison_key===score.comparison_key&&item.score!==null);
  $('#score-value').textContent=score.total===null?'—':score.total.toFixed(1);
  $('#score-change').textContent=current?.change===null||current?.change===undefined?'本次建立分数基线':`较上次 ${signed(current.change)} 分`;
  $('#score-first').textContent=current?.since_first===null||current?.since_first===undefined?'保存后开始记录变化':`较首练 ${signed(current.since_first)} 分`;
  $('#score-best').textContent=eligible.length?`最高 ${Math.max(...eligible.map(item=>item.score)).toFixed(1)} 分 · ${eligible.length} 次同参照练习`:'完成有效录音后开始计分';
  $('#score-note').textContent=score.reason||`参照：${score.reference.name}。${score.method}`;
  $('#generate-corrected').disabled=score.total===null;
  const c=score.curve,r=score.reference;
  $('#standard-overlay').innerHTML=plot({label:'有效发声时间 · 个人相对音高 0–1',draw:(x,y)=>
    `<path class="target-line" d="${path(r.time_normalized,r.pitch_normalized,x,y)}"/><path class="student-line" d="${path(c.time_normalized,c.pitch_normalized,x,y)}"/>`});
  $('#target-legend').textContent=r.kind==='schematic'?'教学目标示意':'真人示范曲线';
  $('#score-source').replaceChildren();
  const source=document.createElement(r.source_url?'a':'span');source.textContent=r.name;
  if(r.source_url){source.href=r.source_url;source.target='_blank';source.rel='noopener noreferrer';}
  $('#score-source').append(source);
  if(r.license){const licence=document.createElement('a');licence.href=r.license_url;licence.textContent=r.license;licence.target='_blank';licence.rel='noopener noreferrer';$('#score-source').append(licence);}
  if(r.attribution){const credit=document.createElement('span');credit.textContent=r.attribution;$('#score-source').append(credit);}
  $('#reference-note').textContent=r.note;
  const labels=['轮廓贴合','升降方向','有效帧比例'],center=[180,124],radius=78;
  const point=(i,value)=>[center[0]+Math.sin(i*2*Math.PI/3)*radius*value/100,center[1]-Math.cos(i*2*Math.PI/3)*radius*value/100];
  const polygon=value=>labels.map((_,i)=>point(i,value).join(',')).join(' ');
  let radar=[25,50,75,100].map(value=>`<polygon class="radar-grid" points="${polygon(value)}"/>`).join('');
  radar+=labels.map((label,i)=>{const p=point(i,134);return `<text class="viz-label" x="${p[0]}" y="${p[1]}" text-anchor="middle">${label} ${score.components[label]??'—'}</text>`;}).join('');
  if(score.total!==null)radar+=`<polygon class="radar-value" points="${labels.map((label,i)=>point(i,score.components[label]).join(',')).join(' ')}"/>`;
  $('#dimension-radar').innerHTML=`<svg viewBox="0 0 360 245" role="img" aria-label="轮廓贴合、升降方向和有效帧比例">${radar}</svg>`;
  $('#deviation-bars').innerHTML=`<svg viewBox="0 0 360 190" role="img" aria-label="前中后段归一化轮廓偏差">${score.deviations.map((part,i)=>{const value=(part.error??0)*100,x=35+i*105;return `<rect class="deviation-bar" x="${x}" y="${145-value}" width="55" height="${value}" rx="5"/><text class="viz-label" x="${x+27}" y="${135-value}" text-anchor="middle">${part.error===null?'—':value.toFixed(1)+'%'}</text><text class="viz-label" x="${x+27}" y="167" text-anchor="middle">${part.label}</text>`;}).join('')}</svg>`;
  $('#energy-chart').innerHTML=result.energy_dbfs?plot({minimum:Math.min(-20,Math.floor(Math.min(...result.energy_dbfs)/10)*10),maximum:0,label:'选区时间比例 · RMS 音量 dBFS',draw:(x,y)=>{
    const start=result.selection.start_s,span=result.selection.end_s-start;
    return `<path class="energy-line" d="${path(result.time_s.map(time=>(time-start)/span),result.energy_dbfs,x,y)}"/>`;
  }}):'<p class="chart-empty-note">新录音会显示实际 RMS 音量曲线。</p>';
  const series=eligible;
  $('#score-trend').innerHTML=plot({minimum:0,maximum:100,label:'同参照练习次序 · 轮廓分',ticks:false,draw:(x,y)=>{
    if(!series.length)return '<text class="viz-label" x="280" y="110" text-anchor="middle">完成有效录音后记录分数</text>';
    const times=series.map((_,i)=>series.length===1?.5:i/(series.length-1));
    return `<path class="student-line" d="${path(times,series.map(item=>item.score),x,y)}"/>`+series.map((item,i)=>`<circle class="score-dot" cx="${x(times[i])}" cy="${y(item.score)}" r="4"><title>第 ${i+1} 次：${item.score} 分</title></circle>`).join('')+
      `<text class="viz-label" x="48" y="207">第 1 次</text><text class="viz-label" x="520" y="207" text-anchor="end">第 ${series.length} 次</text>`;
  }});
  $('#score-records').replaceChildren();
  for(const record of [...records].reverse()){
    const row=document.createElement('tr');
    const values=[new Date(record.created_at*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}),
      record.score===null?'待重新录音':record.score.toFixed(1),record.change===null?'—':signed(record.change),record.reference_name];
    values.forEach(value=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell);});$('#score-records').append(row);
  }
}

document.querySelectorAll('[data-viz-tab]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('[data-viz-tab]').forEach(tab=>tab.setAttribute('aria-selected',String(tab===button)));
  document.querySelectorAll('[data-viz-view]').forEach(panel=>panel.hidden=panel.dataset.vizView!==button.dataset.vizTab);
}));
