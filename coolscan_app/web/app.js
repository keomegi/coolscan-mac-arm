let S=null, sel=new Set(), lastV=-1, stamp=Date.now();
const $=id=>document.getElementById(id);
async function api(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
function selList(){return [...sel].sort((a,b)=>a-b)}
const keys=['film','format','preview_mode','preview_dpi','invert_preview','rotate180','focus_mode','focus_position','exposure_mode','white_balance','dpi','samples','ir','clean','negatives','archive','outdir','basename','digits','start','photo_date','colour_space','name_date','tone','stop_on_errors','eject_after','scan_log','auto_thumbnail','dust_method','grain','restore','notify_mac','notify_shortcut','notify_ntfy'];
let picking=false;
keys.forEach(k=>{const e=$('s-'+k);e.addEventListener('change',()=>{const v=e.type=='checkbox'?e.checked:e.value;api('/api/settings',{[k]:v})})});
$('b-fpoint').onclick=()=>{if(S.settings.focus_point){api('/api/settings',{focus_point:null});return}picking=!picking;document.body.classList.toggle('picking',picking);$('fpoint').textContent=picking?'click a preview…':'centre'};
$('b-connect').onclick=()=>api('/api/connect');$('b-load').onclick=()=>api('/api/load');$('b-eject').onclick=()=>{sel.clear();api('/api/eject')};
$('b-thumb').onclick=()=>{sel.clear();api('/api/thumbnail')};$('b-preview').onclick=()=>api('/api/preview',{frames:selList()});
$('b-cal').onclick=()=>api('/api/calibrate',{frame:selList()[0]??0});$('b-af').onclick=()=>api('/api/autofocus',{frame:selList()[0]??0});
$('b-ae').onclick=()=>api('/api/autoexpose',{frame:selList()[0]??0});$('b-unlock').onclick=()=>api('/api/unlock');
const rollKeys=['film_brand','film_stock','film_iso','film_notes','film_description'];
let descHold=0;
$('rolldesc').addEventListener('change',()=>{descHold=Date.now()+1500;api('/api/describe',{text:$('rolldesc').value})});
function rollnoDone(){$('rollnodlg').hidden=true;$('b-rollnoyes').hidden=true}
async function rollno(move){const n=+$('rollno').value;const r=await api('/api/rollnumber',{number:n,move_aside:move});
 if(r.ok){rollnoDone();return}
 $('rollnodlg').hidden=false;$('b-rollnoyes').hidden=!r.in_the_way;
 if(r.in_the_way)$('b-rollnoyes').textContent=r.in_the_way.length>1?'Move them and renumber':'Move it and renumber';
 if(r.busy)$('rollnomsg').textContent='Wait until the scan finishes, then try again.';
 else if(r.in_the_way){const pad=String(n).padStart(3,'0');
  $('rollnomsg').textContent='Nothing was changed yet. To count on from Roll '+pad+', '+(r.in_the_way.length>1?'these older folders':'this older folder')+' would be moved into "'+r.aside+'" (nothing is deleted or renamed inside): '+r.in_the_way.map(d=>d.name+' ('+d.files+' file'+(d.files==1?'':'s')+')').join(', ')+'.'}
 else $('rollnomsg').textContent=r.error||'That did not work.'}
$('b-rollno').onclick=()=>rollno(false);$('b-rollnoyes').onclick=()=>rollno(true);
$('b-rollnono').onclick=()=>{rollnoDone();$('rollno').value=S.roll_number||''};
$('b-newroll').onclick=()=>{rollKeys.forEach(k=>$('r-'+k).value='');$('rolldlg').hidden=false;$('b-newroll').hidden=true;$('r-film_brand').focus()};
$('b-rollcancel').onclick=()=>{$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-rollok').onclick=async()=>{const b={};rollKeys.forEach(k=>b[k]=$('r-'+k).value);
 const r=await api('/api/newroll',b);if(r.busy){$('rollfilm').textContent='Wait until the scan finishes, then try again.';return}$('rolldlg').hidden=true;$('b-newroll').hidden=false};
$('b-notifytest').onclick=()=>api('/api/notifytest');
async function shortcutNames(){try{const r=await (await fetch('/api/shortcuts')).json();$('shortcutlist').innerHTML=r.names.map(n=>'<option value="'+n.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;')+'">').join('')}catch(e){}}
$('s-notify_shortcut').addEventListener('focus',shortcutNames);
$('rollpick').onchange=async()=>{const r=await api('/api/switchroll',{name:$('rollpick').value});const m=$('rollpickmsg');
 m.hidden=!!r.ok;if(!r.ok){m.textContent=r.busy?'Wait until the scan finishes, then pick the roll again.':(r.error||'That did not work.');$('rollpick').value=S.settings.roll}};
function clock(s){s=Math.max(0,Math.round(s));return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')}
let lastStrip=null;
$('b-scan').onclick=()=>api('/api/scan',{frames:selList()});$('b-stop').onclick=()=>api('/api/stop');
let anchor=null;
function toggle(i,ev){ev=ev||{};
 if(ev.shiftKey&&anchor!==null){const [a,b]=[Math.min(anchor,i),Math.max(anchor,i)];if(!(ev.metaKey||ev.ctrlKey))sel.clear();for(let k=a;k<=b;k++)sel.add(k)}
 else if(ev.metaKey||ev.ctrlKey){sel.has(i)?sel.delete(i):sel.add(i);anchor=i}
 else{const only=sel.size===1&&sel.has(i);sel.clear();if(!only)sel.add(i);anchor=i}
 render(true)}
// focus point: stored in the scanner's orientation, shown in each frame's orientation
function toScreen(p,k){let [x,y]=p;for(let n=0;n<k;n++)[x,y]=[y,1-x];return [x,y]}
function toScanner(p,k){let [x,y]=p;for(let n=0;n<k;n++)[x,y]=[1-y,x];return [x,y]}
$('b-rl').onclick=()=>api('/api/rotate',{frames:selList().length?selList():S.frames,quarter:1});
$('b-rr').onclick=()=>api('/api/rotate',{frames:selList().length?selList():S.frames,quarter:3});
function fillSelect(id,vals,cur,lab){const e=$(id);const want=vals.map(String).join();if(e.dataset.v!==want){e.innerHTML=vals.map(v=>`<option value="${v}">${lab?lab(v):v}</option>`).join('');e.dataset.v=want}e.value=String(cur)}
function render(force){
 if(!S)return; const st=S.settings, c=S.caps, busy=!!S.job;
 // a selection belongs to the strip it was made on: another strip, or fewer frames, and it goes
 if(S.strip!==lastStrip){if(lastStrip!==null){sel.clear();anchor=null}lastStrip=S.strip}
 for(const i of [...sel])if(i>=S.frames.length)sel.delete(i);
 $('status').title=c&&c.revision?'Firmware '+c.revision:'';$('status').textContent=(S.demo?'DEMO · ':'')+(c?(c.product+' · '+c.optical_dpi+' dpi'+(c.holder?' · '+c.holder:'')+(S.film_in===false?' · no film':'')):'not connected')+(busy?' · '+S.job+'…':'');
 keys.forEach(k=>{const e=$('s-'+k);if(document.activeElement===e)return;if(e.type=='checkbox')e.checked=!!st[k];else if(k=='start')e.value=S.next_number;else if(!['dpi','preview_dpi','samples','format'].includes(k))e.value=st[k]??''});
 const opt=c?c.optical_dpi:2900, lo=c&&c.x_dpi_range?c.x_dpi_range[0]:73;
 const dpis=[];for(let n=1;n<=40;n++){const d=Math.round(opt/n);if(d>=lo)dpis.push(d)}
 fillSelect('s-dpi',dpis,st.dpi);fillSelect('s-preview_dpi',dpis.filter(d=>d<=1000),st.preview_dpi);
 const ms=c?(c.max_samples||1):1;fillSelect('s-samples',[1,2,4,8,16].filter(n=>n<=Math.max(ms,4)),st.samples,v=>v==1?'Off':(v<=ms?v+'x (one pass)':v+'x ('+v+' passes)'));
 $('mshint').textContent=st.samples>ms?('Cleaner shadows by averaging '+Math.min(st.samples,4)+' passes. Each frame takes that many times longer.'):'';
 const fmts=c&&c.film_formats?c.film_formats:[];$('fmtrow').hidden=!fmts.length;fillSelect('s-format',[''].concat(fmts),st.format,v=>v||'From holder');
 for(const id of ['b-load','b-eject','b-thumb','b-preview','b-cal','b-af','b-ae','b-unlock','b-scan','b-connect','b-rl','b-rr','b-newroll','b-rollok','b-rollno','b-rollnoyes','rollpick'])$(id).disabled=busy;$('b-stop').disabled=!busy;
 if(c&&!c.thumbnail){$('s-preview_mode').value='scan';$('s-preview_mode').disabled=true}else $('s-preview_mode').disabled=false;
 $('negrow').hidden=!!st.archive;$('dustrow').hidden=!st.clean;
 const iceopt=$('s-dust_method').querySelector('option[value=ice]');iceopt.hidden=iceopt.disabled=!S.ice_helper;
 const nf=S.frames.length, ns=sel.size;
 $('b-scan').textContent=ns?`Scan ${ns} selected frame${ns>1?'s':''}`:(nf?`Scan all ${nf} frame${nf>1?'s':''}`:'Scan all frames');
 $('b-preview').textContent=ns?`Preview ${ns} selected`:'Preview all';
 if(document.activeElement!==$('rollpick')){const rp=$('rollpick'),want=S.rolls.join('\n');if(rp.dataset.v!==want){rp.innerHTML='';for(const n of S.rolls){const o=document.createElement('option');o.value=n;o.textContent=n;rp.appendChild(o)}rp.dataset.v=want}rp.value=st.roll||''}
 $('rollfilm').textContent=(S.film_text?('Film: '+S.film_text):'No film details')+' · saving in '+(S.roll_path||'')+(st.archive?' (RAW/ and Positive/)':'');
 if(document.activeElement!==$('rollno')&&$('rollnodlg').hidden)$('rollno').value=S.roll_number||'';
 const dsc=$('rolldesc');if(document.activeElement!==dsc&&Date.now()>descHold)dsc.value=st.film_description||'';
 $('nameex').textContent='Sample: '+(st.archive?'RAW/ and Positive/':'')+S.sample_name;
 const fl=S.focus_lock, el=S.exposure_lock, af=!c||c.autofocus;
 $('b-af').hidden=!af;$('posrow').hidden=st.focus_mode!=='manual';
 if(c&&c.focus_range){$('s-focus_position').min=c.focus_range[0];$('s-focus_position').max=c.focus_range[1]}
 if(!picking)$('fpoint').textContent=st.focus_point?st.focus_point.map(v=>v.toFixed(2)).join(', '):'centre';$('b-fpoint').textContent=st.focus_point?'Clear':(picking?'Cancel':'Pick');
 $('calinfo').textContent=[fl?`Focus: ${fl.result} at lens position ${fl.position}${fl.frame!=null?' (frame '+(fl.frame+1)+')':''}.`:(st.focus_mode=='frame'?'Focus: each frame is focused on its first preview or scan.':'Focus: not set yet.'),
  el?`Exposure: ${Object.entries(el.exposures).map(([k,v])=>k[0].toUpperCase()+' '+v).join(', ')}${el.infrared?' (with IR)':''}${el.frame!=null?' from frame '+(el.frame+1):''}.`:'Exposure: not set yet.'].join(' ');
 if(force||S.version!==lastV){
  $('thumb').hidden=!S.thumb;$('nothumb').hidden=S.thumb;
  if(S.thumb)$('thumb').src='/thumb.png?'+S.version;
  const inner=document.querySelector('#thumbwrap .inner');inner.querySelectorAll('.box').forEach(b=>b.remove());
  for(const b of S.boxes){const d=document.createElement('div');d.className='box'+(sel.has(b.i)?' sel':'');Object.assign(d.style,{left:b.x*100+'%',top:b.y*100+'%',width:b.w*100+'%',height:b.h*100+'%'});d.innerHTML='<span>'+(b.i+1)+'</span>';d.onclick=ev=>toggle(b.i,ev);inner.appendChild(d)}
  const fp=st.focus_point;
  $('grid').innerHTML=S.frames.map(i=>`<div class="frame${sel.has(i)?' sel':''}" data-i="${i}">${S.previews.includes(i)?`<div class="imgbox"><div class="imgw"><img src="/preview/${i}.png?${S.version}">${fp?(q=>`<div class="mark" style="left:${q[0]*100}%;top:${q[1]*100}%"></div>`)(toScreen(fp,S.turns[i]||0)):''}</div></div>`:'<div class="hint" style="padding:30px 6px">No preview yet</div>'}<div class="cap">Frame ${i+1}${S.preview_info[i]?' · '+S.preview_info[i]:''}</div></div>`).join('')||'<p class="hint">Load film and press Thumbnail only or Preview.</p>';
  $('grid').querySelectorAll('.frame').forEach(e=>e.onclick=ev=>{
   if(picking&&ev.target.tagName=='IMG'){const r=ev.target.getBoundingClientRect();picking=false;document.body.classList.remove('picking');
    const i=+e.dataset.i;api('/api/settings',{focus_point:toScanner([(ev.clientX-r.left)/r.width,(ev.clientY-r.top)/r.height],S.turns[i]||0)});return}
   toggle(+e.dataset.i,ev)});
  $('log').textContent=S.log.join('\n');$('log').scrollTop=1e9;lastV=S.version}
 const tm=S.timing||{}, tl=$('timing');let tt='';
 if(busy&&tm.strip_started){const left=tm.average?tm.average*(tm.of-tm.done)-(tm.frame_started?Math.min(tm.average,tm.now-tm.frame_started):0):null;
  tt='Frame '+(tm.frame||'')+' ('+Math.min(tm.done+1,tm.of)+' of '+tm.of+') · '+clock(tm.now-(tm.frame_started||tm.now))+' on this frame · '+clock(tm.now-tm.strip_started)+' on this strip'+(left!==null?' · about '+clock(left)+' left':'')+(tm.last_frame?' · last frame took '+clock(tm.last_frame):'')}
 else if(tm.last_strip){const ls=tm.last_strip;tt='Last strip: '+ls.frames+' of '+ls.of+' frame'+(ls.of==1?'':'s')+' in '+clock(ls.seconds)+(ls.average?' ('+clock(ls.average)+' per frame)':'')+', finished '+ls.ended}
 tl.hidden=!tt;tl.textContent=tt;
 const p=S.progress;$('prog').hidden=!p;$('progtxt').hidden=!p;if(p){$('prog').max=p.total||1;$('prog').value=p.done;$('progtxt').textContent=`${S.job}: ${p.phase} ${p.total?Math.round(100*p.done/p.total):0}%`}
}
async function poll(){try{S=await (await fetch('/api/state')).json();render(false)}catch(e){$('status').textContent='app stopped'}setTimeout(poll,500)}
poll();
