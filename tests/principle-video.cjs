const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');

(async()=>{
  const base=process.env.BASE_URL||'http://127.0.0.1:8002';
  require('node:fs').mkdirSync('artifacts',{recursive:true});
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||(process.platform==='win32'?'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe':'/usr/bin/chromium')});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:1050}});
    await page.goto(base);await page.click('#guest-start');
    await page.waitForSelector('#auth-dialog',{state:'hidden'});
    assert.equal(await page.locator('#intro-video,.video-commentary').count(),0);
    const render=async(tone,score,practiceId,autoOpen=true)=>page.evaluate(async args=>{
      const {renderPrincipleVideo}=await import('/assets/principle-video.js');
      renderPrincipleVideo({tone:args.tone,score:{total:args.score}},{autoOpen:args.autoOpen,practiceId:args.practiceId});
    },{tone,score,practiceId,autoOpen});
    const close=async()=>{
      await page.click('[data-close="principle-video-dialog"]');
      assert.equal(await page.locator('#principle-video').evaluate(video=>video.paused&&!video.hasAttribute('src')),true);
    };
    for(const width of [1440,390]){
      await page.setViewportSize({width,height:900});
      for(const tone of [1,2,3,4]){
        const id=`low-${width}-${tone}`;
        await render(tone,79.9,id);
        assert.equal(await page.locator('#principle-video-dialog').evaluate(dialog=>dialog.open),true);
        assert.match(await page.locator('#principle-video').getAttribute('src'),new RegExp(`tone-principle-${tone}\\.mp4$`));
        await page.locator('#principle-video').evaluate(video=>video.play());
        await page.waitForFunction(()=>document.querySelector('#principle-video').currentTime>0.1);
        assert.equal(await page.locator('#principle-video').evaluate(video=>video.error),null);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await close();
        await render(tone,79.9,id);
        assert.equal(await page.locator('#principle-video-dialog').evaluate(dialog=>dialog.open),false);
        for(const score of [80,80.1]){
          await render(tone,score,`high-${width}-${tone}-${score}`);
          assert.equal(await page.locator('#principle-video-dialog').evaluate(dialog=>dialog.open),false);
          assert.equal(await page.locator('#principle-video-entry').isVisible(),true);
          await page.click('#view-principle-video');
          assert.match(await page.locator('#principle-video').getAttribute('src'),new RegExp(`tone-principle-${tone}\\.mp4$`));
          await close();
        }
      }
    }
    await render(4,75,'history',false);
    assert.equal(await page.locator('#principle-video-dialog').evaluate(dialog=>dialog.open),false);
    await page.click('.tone[data-tone="2"]');
    assert.equal(await page.locator('#principle-video-entry').isHidden(),true);
    for(const score of [null,undefined,NaN,-1,101]){
      await render(4,score,'unscored');
      assert.equal(await page.locator('#principle-video-entry').isHidden(),true);
      assert.equal(await page.locator('#principle-video-dialog').evaluate(dialog=>dialog.open),false);
    }
    for(const tone of [1,2,3,4]){
      const response=await page.request.get(`${base}/assets/media/tone-principle-${tone}.mp4`,{headers:{Range:'bytes=0-1023'}});
      assert.equal(response.status(),206);assert.equal((await response.body()).length,1024);
    }
    await render(4,75,'proof-fourth');
    await page.screenshot({path:path.resolve('artifacts/principle-video-fourth-mobile.png')});
    await close();
    console.log('Principle video checks passed: four exact tone mappings, desktop/mobile playback, <80 auto-open, 80/80.1 manual, missing scores, history, tone reset, repeat suppression and close-to-stop.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
