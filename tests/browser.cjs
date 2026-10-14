const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');

async function main(){
  const base=process.env.BASE_URL||'http://127.0.0.1:8000';
  const artifacts=path.resolve('artifacts');fs.mkdirSync(artifacts,{recursive:true});
  const fixture=path.join(artifacts,'synthetic-tone.wav');
  const python=process.env.TEST_PYTHON||(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
  const made=spawnSync(python,['-c',`import wave,numpy as np
sr=44100;t=np.arange(int(sr*1.6))/sr
f=np.interp(t,[0,.2,.7,1.4,1.6],[240,240,155,245,245])
phase=2*np.pi*np.cumsum(f)/sr
y=(.22*np.sin(phase)+.06*np.sin(2*phase))*((t>.15)&(t<1.45))
with wave.open(${JSON.stringify(fixture)},'wb') as w:
 w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((y*32767).astype('<i2').tobytes())
`],{encoding:'utf8'});
  assert.equal(made.status,0,made.stderr);
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||(process.platform==='win32'?undefined:'/usr/bin/chromium'),
    headless:true,args:['--no-sandbox','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',`--use-file-for-fake-audio-capture=${fixture}`]});
  const context=await browser.newContext({viewport:{width:1440,height:1120},timezoneId:'Asia/Shanghai'});
  const page=await context.newPage();const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{
    const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia=async(...args)=>{const stream=await original(...args);window.__lastStream=stream;return stream;};
  });
  try{
    await page.goto(base);await page.waitForSelector('#auth-dialog[open]');
    const setup=await page.locator('#auth-title').textContent();
    await page.fill('#auth-username','preview_teacher');await page.fill('#auth-password','preview-test-password');
    if(setup.includes('创建')){
      await page.fill('#auth-display','演示教师');
      if(process.env.SETUP_TOKEN)await page.fill('#auth-setup',process.env.SETUP_TOKEN);
    }
    await page.click('#auth-submit');await page.waitForSelector('#auth-dialog',{state:'hidden'});
    await page.screenshot({path:path.join(artifacts,'desktop-empty.png'),fullPage:true});
    const initialCount=Number(await page.locator('#history-count').textContent());
    await page.click('#record');await page.waitForSelector('#record.recording');
    await page.waitForTimeout(1700);await page.click('#record');
    await page.waitForFunction(()=>document.querySelector('#preview').hidden===false && !document.querySelector('#analyze').disabled);
    assert.equal(await page.evaluate(()=>window.__lastStream.getTracks().every(track=>track.readyState==='ended')),true);
    const original=await page.locator('#audio').getAttribute('src');assert.match(original,/^blob:/);
    await page.locator('#audio').evaluate(audio=>audio.play());
    assert.equal(await page.locator('#audio').evaluate(audio=>audio.paused),false);
    await page.locator('#audio').evaluate(audio=>audio.pause());
    await page.click('#analyze');await page.waitForFunction(()=>document.querySelector('#flow-status').textContent==='已完成',null,{timeout:30000});
    await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);
    await page.waitForSelector('#science-visuals:not([hidden])');
    assert.equal(await page.locator('#science-quality-plot svg').count(),1);
    assert.equal(await page.locator('#science-regions-plot .science-bar').count(),3);
    assert.ok((await page.locator('#science-body').textContent()).includes('下一轮练习实验'));
    assert.equal(await page.locator('#chat-trace li.completed').count(),6);
    assert.equal(await page.locator('#agent-nodes .completed').count(),6);
    assert.equal(await page.locator('#trace li.completed').count(),8);
    assert.equal(await page.locator('#chart .f0-line').count(),1);
    await page.fill('#chat-input','三声的低音区怎么观察？');await page.click('#chat-send');
    await page.waitForFunction(()=>!document.querySelector('#chat-send').disabled);
    assert.ok(await page.locator('.message-sources').count());
    await page.click('#memory-nav');await page.waitForSelector('#memory-dialog[open]');
    await page.fill('#memory-goal','重点比较三声的两次曲线');await page.click('#memory-form .primary');
    await page.waitForFunction(()=>document.querySelector('#memory-status').textContent.includes('已保存'));
    await page.click('[data-close="memory-dialog"]');
    await page.click('#record');await page.waitForSelector('#record.recording');await page.waitForTimeout(1700);await page.click('#record');
    await page.waitForFunction(()=>document.querySelector('#preview').hidden===false&&!document.querySelector('#analyze').disabled);
    await page.click('#analyze');await page.waitForFunction(()=>document.querySelector('#flow-status').textContent==='已完成',null,{timeout:30000});
    await page.waitForFunction(()=>!document.querySelector('#analyze').disabled);await page.check('#compare');
    assert.equal(await page.locator('#science-comparison > div').count(),3);
    assert.equal(await page.locator('#chart .previous-line').count(),1);
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:path.join(artifacts,'desktop-analysis.png'),fullPage:true});
    await page.reload();await page.waitForFunction(count=>Number(document.querySelector('#history-count').textContent)===count,initialCount+2);
    await page.click('#memory-nav');await page.waitForSelector('#memory-dialog[open]');
    assert.equal(await page.locator('#memory-goal').inputValue(),'重点比较三声的两次曲线');
    await page.click('[data-close="memory-dialog"]');
    await page.locator('.history-item').first().click();await page.waitForFunction(()=>!document.querySelector('#export').disabled);
    assert.equal(await page.locator('#chart .f0-line').count(),1);
    await page.setViewportSize({width:390,height:844});
    await page.click('#science-generate');await page.waitForFunction(()=>!document.querySelector('#science-generate').disabled);
    await page.screenshot({path:path.join(artifacts,'mobile-analysis.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    await page.evaluate(()=>navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('denied','NotAllowedError')));
    await page.click('#record');await page.waitForFunction(()=>document.querySelector('#record-status').textContent.includes('未获授权'));
    assert.equal(await page.locator('#upload').isEnabled(),true);
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: recording, final audio, microphone release, Praat, 8 harness stages, chat, memory, repeat comparison, reload history, mobile layout, permission failure.');
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
