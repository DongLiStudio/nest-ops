/* Homepage custom.js: load one read-only NestOps extension. */
(() => {
 if(window.__nestOpsBootstrap)return;window.__nestOpsBootstrap=true;
 document.documentElement.lang='zh-CN';document.documentElement.setAttribute('translate','no');
 const style=document.createElement('link');style.rel='stylesheet';style.href='/_ops/ui.css?v=20261015-complete';document.head.append(style);const asset='/_ops/ui.js?v=20261015-complete';let script,timer;
 function failure(){
  if(document.querySelector('#home-console')||document.querySelector('#home-console-load-error'))return;
  const footer=document.querySelector('#footer');if(!footer)return;
  const surface=document.createElement('section');surface.id='home-console-load-error';surface.className='home-console';surface.setAttribute('role','status');
  const text=document.createElement('p');text.textContent='服务目录暂时无法加载，请重试。';
  const retry=document.createElement('button');retry.type='button';retry.className='ops-button';retry.textContent='重新加载';retry.onclick=()=>{surface.remove();load();};surface.append(text,retry);footer.before(surface);
 }
 function load(){
  clearTimeout(timer);if(document.querySelector('#home-console'))return;
  if(script)script.remove();script=document.createElement('script');script.src=asset;script.async=true;script.dataset.nestOpsConsole='';script.onerror=failure;
  script.onload=()=>clearTimeout(timer);document.head.append(script);timer=setTimeout(failure,20000);
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
