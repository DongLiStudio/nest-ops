/* Homepage custom.js: load one read-only NestOps extension. */
(() => {
 if(window.__nestOpsBootstrap)return;window.__nestOpsBootstrap=true;
 document.documentElement.lang='zh-CN';document.documentElement.setAttribute('translate','no');
 const asset='/_ops/ui.js?v=20261016-paint';let script,timer,style,stylesReady=false,loading=false;
 function failure(){
  if(document.querySelector('#home-console')||document.querySelector('#home-console-load-error'))return;
  const footer=document.querySelector('#footer');if(!footer)return;
  const surface=document.createElement('section');surface.id='home-console-load-error';surface.className='home-console';surface.style.cssText='margin:28px auto;padding:24px;max-width:1108px;color:#f4f9fd;background:#142b3b;border-radius:22px';surface.setAttribute('role','status');
  const text=document.createElement('p');text.textContent='服务目录暂时无法加载，请重试。';
  const retry=document.createElement('button');retry.type='button';retry.className='ops-button';retry.textContent='重新加载';retry.onclick=()=>{surface.remove();load();};surface.append(text,retry);footer.before(surface);
 }
 function loadScript(){
  if(script)script.remove();script=document.createElement('script');script.src=asset;script.async=true;script.dataset.nestOpsConsole='';
  script.onerror=()=>{loading=false;clearTimeout(timer);failure();};
  script.onload=()=>{loading=false;clearTimeout(timer);};document.head.append(script);
 }
 function load(){
  if(loading||document.querySelector('#home-console'))return;
  loading=true;clearTimeout(timer);
  timer=setTimeout(()=>{loading=false;if(style&&!stylesReady){style.onload=null;style.remove();}if(script)script.remove();failure();},20000);
  if(stylesReady){loadScript();return;}
  if(style)style.remove();style=document.createElement('link');style.rel='stylesheet';style.href='/_ops/ui.css?v=20261016-paint';
  // Never construct visible skeletons before their layout CSS is available.
  style.onload=()=>{stylesReady=true;loadScript();};
  style.onerror=()=>{loading=false;clearTimeout(timer);failure();};document.head.append(style);
 }
 load();
 // Keep the original user gesture when invoking the installed native launcher.
 document.addEventListener('click',event=>{
  const link=event.target.closest?.('a[href^="hometunnel://connect"]');
  if(link&&event.button===0&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey)event.stopImmediatePropagation();
 },true);
 // Homepage continues to own weather and coordinates. Request at most once,
 // only while permission is still prompt; denial never causes repeated prompts.
 if(!window.isSecureContext||!navigator.geolocation)return;
 let remaining=60,checking=false,stopped=false;
 async function weather(){
  if(stopped||checking)return;const control=document.querySelector('.information-widget-openmeteo-location-button');
  if(!control){if(--remaining>0)setTimeout(weather,500);return;}
  checking=true;
  try{let state='prompt';try{if(navigator.permissions?.query)state=(await navigator.permissions.query({name:'geolocation'})).state;}catch{}
   stopped=true;if(state==='prompt'&&control.isConnected)control.click();
  }finally{checking=false;}
 }
 if(document.readyState==='complete')void weather();else window.addEventListener('load',weather,{once:true});
})();
