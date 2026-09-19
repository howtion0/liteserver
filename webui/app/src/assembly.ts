import './forge.css';
import './laptop.css';
import './tower.css';
import './retro.css';
import './server-console.css';
import './desk.css';
import {installRadio} from './radio';
import {installServerConsole} from './server-console';
import {colorWheel} from './color-wheel';
import * as T from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {STLLoader} from 'three/addons/loaders/STLLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {DEFAULT_ASSEMBLY,MOUNTS,JOINTS,HOME,PIVOTS,type Joint,type Pose} from './assembly-config';
import {GifTexture} from './gif-texture';
import {JsScreen, EXAMPLE} from './js-screen';
import {createMotion,MOTIONS} from './otto-motion';
const el=<E extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as E;
const checked=(id:string)=>el<HTMLInputElement>(id).checked;
const viewport=el('assembly-viewport');
const laptop=document.createElement('div');laptop.className='laptop-frame';
viewport.before(laptop);laptop.append(viewport,document.querySelector('.stage-header')!);
laptop.append(document.querySelector('.stage-toolbar')!);
const laptopArt=document.createElement('img');laptopArt.src='./art/forge-retro-monitor.png';laptopArt.alt='手绘复古 CRT 台式显示器与键盘';laptopArt.className='laptop-art';laptop.prepend(laptopArt);
const tower=document.querySelector('aside')!;tower.classList.add('tower-frame');
const towerFace=document.createElement('div');towerFace.className='tower-face';
while(tower.firstChild)towerFace.append(tower.firstChild);
const towerArt=document.createElement('img');towerArt.src='./art/forge-retro-tower.png';towerArt.alt='手绘复古电脑主机控制台';towerArt.className='tower-art';tower.append(towerArt,towerFace);
const app=document.querySelector<HTMLElement>('.assembly-app')!;
const sizeNote=document.createElement('div');sizeNote.className='tower-size-note';
const sizeNoteArt=document.createElement('img');sizeNoteArt.src='./art/forge-size-sticky-note.png';sizeNoteArt.alt='';sizeNoteArt.setAttribute('aria-hidden','true');
const sizeNoteText=document.createElement('span');sizeNoteText.textContent='按一下变换尺寸 →';
sizeNote.append(sizeNoteArt,sizeNoteText);
const sizeToggle=document.createElement('button');sizeToggle.type='button';sizeToggle.className='tower-size-toggle';sizeToggle.setAttribute('aria-label','放大机箱调试区');sizeToggle.setAttribute('aria-pressed','false');sizeToggle.title='切换机箱调试区尺寸';
let towerExpanded=false;
sizeToggle.onclick=()=>{sizeToggle.classList.remove('is-pressed');void sizeToggle.offsetWidth;sizeToggle.classList.add('is-pressed');towerExpanded=!towerExpanded;app.classList.toggle('tower-expanded',towerExpanded);sizeToggle.setAttribute('aria-pressed',String(towerExpanded));sizeToggle.setAttribute('aria-label',towerExpanded?'恢复机箱原始尺寸':'放大机箱调试区');};
sizeToggle.addEventListener('animationend',()=>sizeToggle.classList.remove('is-pressed'));
sizeToggle.append(sizeNote);tower.append(sizeToggle);
installServerConsole();
installRadio(laptop,tower,viewport);
const motionHint=document.querySelector('[data-pane="motion"] .hint');if(motionHint)motionHint.textContent='上推增加角度 · 本地仿真';
const renderer=new T.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFShadowMap;
renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=.75;viewport.append(renderer.domElement);
const scene=new T.Scene();scene.background=new T.Color('#111318');
const pmrem=new T.PMREMGenerator(renderer);const room=new RoomEnvironment();scene.environment=pmrem.fromScene(room).texture;room.dispose();pmrem.dispose();
const camera=new T.PerspectiveCamera(38,1,.1,2000);camera.position.set(190,145,350);
const controls=new OrbitControls(camera,renderer.domElement);controls.target.set(0,80,0);controls.enableDamping=true;controls.minDistance=90;controls.maxDistance=750;
scene.add(new T.HemisphereLight(0xffffff,0x9c9876,1));
const sun=new T.DirectionalLight(0xffffff,1.5);sun.position.set(140,260,180);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);
Object.assign(sun.shadow.camera,{left:-180,right:180,top:220,bottom:-180,near:1,far:650});sun.shadow.bias=-.001;scene.add(sun);
const ground=new T.Mesh(new T.PlaneGeometry(1400,1400),new T.ShadowMaterial({opacity:.3}));ground.rotation.x=-Math.PI/2;ground.position.y=-.2;ground.receiveShadow=true;scene.add(ground);
const grid=new T.GridHelper(420,21,0x304675,0x14264a);grid.position.y=-.15;scene.add(grid);
const rig=new T.Group();scene.add(rig);
const gold=new T.MeshStandardMaterial({color:'#e8af06',roughness:.64,metalness:.02,envMapIntensity:.35});
const white=new T.MeshStandardMaterial({color:'#e6e9e5',roughness:.62,envMapIntensity:.5});
const gif=new GifTexture();const screen=new T.Mesh(new T.PlaneGeometry(29,31),new T.MeshBasicMaterial({map:gif.texture,toneMapped:false}));screen.position.set(0,71,34.05);rig.add(screen);
let screenMode='gif';
let jsStatus='未运行';
const jsScreen=new JsScreen(message=>{jsStatus=message;el('js-status').textContent=message;});
el<HTMLTextAreaElement>('js-code').value=EXAMPLE;
const codeDialog=document.createElement('dialog');codeDialog.className='screen-code-dialog';
const codeHeading=document.createElement('h2');codeHeading.textContent='屏幕 JavaScript';codeHeading.id='screen-code-heading';codeDialog.setAttribute('aria-labelledby',codeHeading.id);
const closeCode=document.createElement('button');closeCode.textContent='关闭编辑器';closeCode.onclick=()=>codeDialog.close();
codeDialog.append(codeHeading,closeCode,el('js-editor'));document.body.append(codeDialog);
const openCode=document.createElement('button');openCode.className='open-code';openCode.textContent='打开代码编辑器';
el('screen-mode').closest('label')!.after(openCode);
openCode.onclick=()=>{selectScreen('js');if(!codeDialog.open)codeDialog.showModal();};
function selectScreen(mode:string,openEditor=true){screenMode=mode;el<HTMLSelectElement>('screen-mode').value=mode;el('js-editor').hidden=mode!=='js';el('gif-controls').hidden=mode==='js';screen.material.map=mode==='js'?jsScreen.texture:gif.texture;screen.material.needsUpdate=true;if(mode==='gif'){jsScreen.stop();codeDialog.close();}else if(openEditor&&!codeDialog.open)codeDialog.showModal();}
el('screen-mode').onchange=e=>{const mode=(e.target as HTMLSelectElement).value;selectScreen(mode);if(mode==='js')jsScreen.run(el<HTMLTextAreaElement>('js-code').value);};
el('js-run').onclick=()=>{selectScreen('js');jsScreen.run(el<HTMLTextAreaElement>('js-code').value);};
el('js-stop').onclick=()=>{jsScreen.stop();jsStatus='已停止 · 保留最后一帧';el('js-status').textContent=jsStatus;};
el('js-example').onclick=()=>{el<HTMLTextAreaElement>('js-code').value=EXAMPLE;};
window.addEventListener('pagehide',()=>jsScreen.dispose(),{once:true});
const shellRoot=new T.Group();rig.add(shellRoot);const bodyRoot=new T.Group();rig.add(bodyRoot);
const gripRoot=new T.Group();rig.add(gripRoot);
const pivots={} as Record<Joint,T.Group>;for(const {key} of JOINTS)pivots[key]=new T.Group();
rig.add(pivots.ll,pivots.rl);pivots.ll.add(pivots.lf);pivots.rl.add(pivots.rf);
const axes:T.AxesHelper[]=[];for(const {key} of JOINTS){const axis=new T.AxesHelper(17);pivots[key].add(axis);axes.push(axis);}
let config={...DEFAULT_ASSEMBLY};let pose:Pose={...HOME};let action='home';let spinning=false;let explode=0;let loaded=false;
let motion:ReturnType<typeof createMotion>|null=null;let motionTime=0;
const shells:Record<string,T.Mesh>={};const parts:Record<string,T.Mesh[]>={shell:[],body:[],leg:[],foot:[],grip:[]};
const deg=T.MathUtils.degToRad;
function mesh(geometry:T.BufferGeometry,material:T.Material){const m=new T.Mesh(geometry,material);m.castShadow=true;m.receiveShadow=true;return m;}
function transform(g:T.BufferGeometry,x:number,y:number,z:number,offset:number[]){return g.clone().applyMatrix4(new T.Matrix4().makeRotationFromEuler(new T.Euler(x,y,z))).translate(offset[0],offset[1],offset[2]);}
function mirror(g:T.BufferGeometry){const c=g.clone().scale(-1,1,1);const p=c.attributes.position;const n=c.attributes.normal;
 for(let i=0;i<p.count;i+=3)for(const a of [p,n]){const v=new T.Vector3().fromBufferAttribute(a,i);a.setXYZ(i,a.getX(i+2),a.getY(i+2),a.getZ(i+2));a.setXYZ(i+2,v.x,v.y,v.z);}return c;}
function jointMesh(g:T.BufferGeometry,key:Joint){const origin=new T.Vector3(...PIVOTS[key]);g.translate(-origin.x,-origin.y,-origin.z);const m=mesh(g,white);pivots[key].add(m);return m;}
function applyMounts(){
 pivots.ll.position.set(...PIVOTS.ll);pivots.rl.position.set(...PIVOTS.rl);
 for(const key of ['lf','rf'] as const){const parent=key==='lf'?'ll':'rl';pivots[key].position.copy(new T.Vector3(...PIVOTS[key]).sub(new T.Vector3(...PIVOTS[parent])));pivots[key].position.y-=explode*30;}
 pivots.ll.position.x+=explode*30;pivots.rl.position.x-=explode*30;
 shellRoot.position.set(0,config.shellHeight+explode*95,config.shellDepth);shellRoot.rotation.y=deg(config.shellYaw);
 bodyRoot.position.set(0,explode*35,config.bodyDepth);gripRoot.position.set(0,explode*55,0);screen.position.set(0,71+explode*35,34.05+config.bodyDepth);
}
function visibility(){const mode=el<HTMLSelectElement>('inspect').value;
 for(const [name,meshes] of Object.entries(parts))for(const m of meshes)m.visible=(mode==='assembly'?(name==='leg'||name==='foot'||checked('show-'+name)):mode===name);
 for(const [key,m] of Object.entries(shells))m.visible=m.visible&&key===el<HTMLSelectElement>('shell').value;
 screen.visible=mode==='assembly'&&checked('show-screen');axes.forEach(a=>a.visible=checked('show-axes'));
 const mat=shells.a?.material as T.MeshStandardMaterial|undefined;if(mat){mat.transparent=checked('transparent');mat.opacity=mat.transparent?.25:1;mat.depthWrite=!mat.transparent;}
}
function updatePose(){for(const {key} of JOINTS){if(key==='ll'||key==='rl')pivots[key].rotation.y=deg((90-pose[key]));else pivots[key].rotation.z=deg((90-pose[key]));el<HTMLInputElement>('joint-'+key).value=String(pose[key]);const number=el<HTMLInputElement>('number-joint-'+key);if(number&&document.activeElement!==number)number.value=pose[key].toFixed(0);el('value-'+key).textContent=pose[key].toFixed(0)+'°';}renderer.domElement.dataset.pose=JSON.stringify(pose);}
function home(){action='home';pose={...HOME};rig.rotation.z=0;updatePose();document.querySelectorAll('[data-action]').forEach(b=>b.classList.toggle('selected',(b as HTMLElement).dataset.action==='home'));}
function cameraView(x:number,z:number){spinning=false;rig.rotation.y=0;camera.position.set(x,110,z);controls.target.set(0,80,0);controls.update();el('orbit').textContent='▶ 自动旋转';el('orbit').setAttribute('aria-pressed','false');}
async function start(){
 const loader=new STLLoader();const names=['nailong-a','nailong-b','body-clean','leg','foot','grip'];const geometries=await Promise.all(names.map(n=>loader.loadAsync('./models/'+n+'.stl')));
 const shellMaterial=gold.clone();for(const [i,key] of ['a','b'].entries()){const g=geometries[i].clone().rotateX(-Math.PI/2);g.computeBoundingBox();const box=g.boundingBox!;const center=box.getCenter(new T.Vector3());g.translate(-center.x,-box.min.y,-center.z);shells[key]=mesh(g,shellMaterial);shellRoot.add(shells[key]);parts.shell.push(shells[key]);}
 // Rigid source transforms fitted against public GLB; no independent mesh rescaling.
 // Clean user body: 69 × 69 × 42; align directly to official body_no_hands.
 const body=mesh(transform(geometries[2],-Math.PI/2,0,0,[-9.327,50,0]),gold);bodyRoot.add(body);parts.body.push(body);
 const leg=transform(geometries[3],-Math.PI/2,0,0,[0,9,24]);parts.leg.push(jointMesh(leg.clone(),'ll'),jointMesh(mirror(leg),'rl'));
 const foot=transform(geometries[4],-Math.PI/2,0,Math.PI,[57,0,-32]);parts.foot.push(jointMesh(foot.clone(),'lf'),jointMesh(mirror(foot),'rf'));
 const grip=geometries[5].clone().rotateX(-Math.PI/2);grip.computeBoundingBox();const c=grip.boundingBox!.getCenter(new T.Vector3());grip.translate(-c.x,-c.y,-c.z);
 for(const x of [-25,25]){const m=mesh(grip.clone(),white);m.position.set(x,68,-15);gripRoot.add(m);parts.grip.push(m);}
 geometries.forEach(g=>g.dispose());loaded=true;applyMounts();visibility();updatePose();applyColors();cameraView(135,345);el('assembly-loading').remove();el('load-status').textContent='就绪 · 4 关节';renderer.domElement.dataset.loaded='true';await setFace('./faces/neutral.gif');
}
for(const {key,label} of JOINTS){const row=document.createElement('label');row.className='range-field';row.title=label;row.innerHTML=`<span>${label.split(' · ')[0]}<output id="value-${key}">90°</output></span><input aria-label="${label}" id="joint-${key}" type="range" min="0" max="180" value="90">`;el('joint-controls').append(row);el('joint-'+key).addEventListener('input',e=>{action='manual';pose[key]=Number((e.target as HTMLInputElement).value);document.querySelectorAll('[data-action]').forEach(b=>b.classList.remove('selected'));updatePose();});}
for(const {key,label,min,max,step=1} of MOUNTS){const row=document.createElement('label');row.className='range-field';row.innerHTML=`<span>${label}<output id="mount-value-${key}">${config[key]}</output></span><input aria-label="${label}" id="mount-${key}" type="range" min="${min}" max="${max}" step="${step}" value="${config[key]}">`;el('assembly-controls').append(row);el('mount-'+key).addEventListener('input',e=>{config[key]=Number((e.target as HTMLInputElement).value);el('mount-value-'+key).textContent=String(config[key]);applyMounts();});}
el('reset-assembly').onclick=()=>{config={...DEFAULT_ASSEMBLY};for(const {key} of MOUNTS){el<HTMLInputElement>('mount-'+key).value=String(config[key]);el<HTMLInputElement>('number-mount-'+key).value=String(config[key]);el('mount-value-'+key).textContent=String(config[key]);}applyMounts();};
el('export-assembly').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({source:'https://ottodiy.tech/files/models/otto.glb',pivots:PIVOTS,shell:el<HTMLSelectElement>('shell').value,mounts:config,pose,colors},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='forge-assembly.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
for(const id of ['inspect','shell','show-shell','show-body','show-screen','show-grip','transparent','show-axes'])el(id).addEventListener('change',visibility);
el('explode').oninput=e=>{explode=Number((e.target as HTMLInputElement).value);el('explode-value').textContent=Math.round(explode*100)+'%';applyMounts();};
el('motion-speed').oninput=e=>el('motion-speed-value').textContent=Number((e.target as HTMLInputElement).value).toFixed(1)+'×';
document.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(b=>b.onclick=()=>{if(b.dataset.action==='home'){home();el('motion-status').textContent='已复位';}else playMotion(b.dataset.action!);document.querySelectorAll('[data-action]').forEach(x=>x.classList.toggle('selected',x===b));});
el('orbit').onclick=()=>{spinning=!spinning;el('orbit').textContent=spinning?'Ⅱ 暂停自转':'▶ 自动旋转';el('orbit').setAttribute('aria-pressed',String(spinning));};
el('front').onclick=()=>cameraView(0,360);el('side').onclick=()=>cameraView(360,0);el('back').onclick=()=>cameraView(0,-360);el('fit').onclick=()=>cameraView(170,350);
el('wireframe').onclick=()=>{const active=el('wireframe').getAttribute('aria-pressed')!=='true';el('wireframe').setAttribute('aria-pressed',String(active));[gold,white,...Object.values(shells).map(m=>m.material as T.MeshStandardMaterial)].forEach(m=>m.wireframe=active);};
async function setFace(url:string,strict=false){try{await gif.load(url);el('face-status').textContent=`GIF 动画 · ${gif.frameCount} 帧`;renderer.domElement.dataset.gifFrames=String(gif.frameCount);}catch(e){el('face-status').textContent='表情载入失败：'+String(e);if(strict)throw e;}}
el('face-select').onchange=e=>{selectScreen('gif');void setFace('./faces/'+(e.target as HTMLSelectElement).value+'.gif');};
el('face-file').onchange=async e=>{const file=(e.target as HTMLInputElement).files?.[0];if(!file)return;if(file.size>10_000_000){el('face-status').textContent='请选择小于 10 MB 的 GIF';return;}const url=URL.createObjectURL(file);await setFace(url);URL.revokeObjectURL(url);};
new ResizeObserver(()=>{const w=viewport.clientWidth,h=viewport.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}).observe(viewport);
let previous=performance.now();renderer.setAnimationLoop(now=>{const dt=Math.min((now-previous)/1000,.05);previous=now;
 if(loaded){if(spinning)rig.rotation.y+=dt*.24;if(action!=='home'&&action!=='manual'&&motion){motionTime+=dt*1000*Number(el<HTMLInputElement>('motion-speed').value);pose=motion.sample(motionTime);updatePose();el('motion-status').textContent=`${MOTIONS.find(m=>m[0]===action)?.[1]||action} · ${(motionTime/1000).toFixed(1)} / ${(motion.duration/1000).toFixed(1)} s`;if(motionTime>=motion.duration){home();el('motion-status').textContent='动作完成 · 已复位';}}gif.update(dt);renderer.domElement.dataset.gifFrame=String(gif.frame);renderer.domElement.dataset.rotation=String(rig.rotation.y);}
 if(screenMode==='js')jsScreen.update(dt);
 controls.update();renderer.render(scene,camera);
});
start().catch(e=>{el('load-status').textContent='加载失败';el('assembly-loading').textContent=String(e);console.error(e);});

// Numeric controls share the original input events, so every path updates one pose.
document.querySelectorAll<HTMLInputElement>('input[type=range]').forEach(range=>{
 if(range.id.startsWith('joint-'))return;
 const row=document.createElement('div');row.className='number-control';
 const minus=document.createElement('button');minus.type='button';minus.textContent='−';minus.setAttribute('aria-label','减少 '+(range.getAttribute('aria-label')||range.id));
 const plus=document.createElement('button');plus.type='button';plus.textContent='+';plus.setAttribute('aria-label','增加 '+(range.getAttribute('aria-label')||range.id));
 const number=document.createElement('input');number.type='number';number.id='number-'+range.id;number.min=range.min;number.max=range.max;number.step=range.step||'1';number.value=range.value;number.setAttribute('aria-label',(range.getAttribute('aria-label')||range.id)+' 数值');
 const commit=()=>{const value=number.valueAsNumber;if(!Number.isFinite(value))return;const bounded=Math.min(Number(range.max),Math.max(Number(range.min),value));range.value=String(bounded);number.value=range.value;range.dispatchEvent(new Event('input',{bubbles:true}));};
 number.oninput=()=>{if(number.validity.valid)commit();};number.onchange=()=>{if(number.value==='')number.value=range.value;commit();};
 number.onkeydown=e=>{if(e.key==='Enter'){commit();number.blur();}};
 minus.onclick=()=>{number.value=String(Number(range.value)-Number(number.step));commit();};plus.onclick=()=>{number.value=String(Number(range.value)+Number(number.step));commit();};
 range.addEventListener('input',()=>{if(document.activeElement!==number)number.value=range.value;});row.append(minus,number,plus);range.after(row);
});
el('pause-motion').onclick=()=>{action='manual';el('motion-status').textContent='已暂停 · 保持角度';document.querySelectorAll('[data-action]').forEach(b=>b.classList.remove('selected'));};
document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(button=>button.onclick=()=>{
 towerFace.scrollTop=0;
 document.querySelectorAll<HTMLElement>('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==button.dataset.tab);
 document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});
});
const defaultColors={shell:'#e8af06',body:'#e8af06',limbs:'#e6e9e5'};let colors={...defaultColors};
type ColorPart=keyof typeof colors;
function applyColors(){
 gold.color.set(colors.body);white.color.set(colors.limbs);
 for(const m of Object.values(shells))(m.material as T.MeshStandardMaterial).color.set(colors.shell);
 renderer.domElement.dataset.colors=JSON.stringify(colors);
}
let syncWheel:(hex:string)=>void=()=>{};
function setColor(part:ColorPart,value:string){if(!/^#[0-9a-f]{6}$/i.test(value))return;colors[part]=value;el<HTMLInputElement>('color-'+part).value=value;el<HTMLInputElement>('hex-'+part).value=value;applyColors();const target=el<HTMLSelectElement>('color-target').value;if(target===part||target==='all')syncWheel(value);}
for(const [key,label] of Object.entries({shell:'外壳',body:'机身',limbs:'腿脚'})){
 const part=key as ColorPart;const row=document.createElement('div');row.className='color-row';
 row.innerHTML=`<label for="color-${part}">${label}</label><input id="color-${part}" aria-label="${label}颜色" type="color" value="${colors[part]}"><input id="hex-${part}" aria-label="${label} HEX" type="text" value="${colors[part]}" pattern="#[0-9a-fA-F]{6}" maxlength="7">`;
 el('color-controls').append(row);el('color-'+part).oninput=e=>setColor(part,(e.target as HTMLInputElement).value);
 el('hex-'+part).oninput=e=>{const input=e.target as HTMLInputElement;input.setCustomValidity(/^#[0-9a-f]{6}$/i.test(input.value)?'':'请输入 # 加六位颜色值');setColor(part,input.value);};
}
const palette=['#ffffff','#e6e9e5','#64748b','#111827','#e8af06','#ff7a00','#ef4444','#ff69b4','#a855f7','#7360f5','#247bff','#38bdf8','#06b6d4','#10b981','#84cc16','#8b5e3c'];
const wheelHost=document.createElement('div');wheelHost.className='color-wheel-host';el('palette').before(wheelHost);
wheelHost.before(el('color-target').closest('label')!);
syncWheel=colorWheel(wheelHost,value=>{const target=el<HTMLSelectElement>('color-target').value;for(const part of (target==='all'?Object.keys(colors):[target]) as ColorPart[])setColor(part,value);});
el('color-target').onchange=()=>syncWheel(colors[el<HTMLSelectElement>('color-target').value as ColorPart]||colors.shell);syncWheel(colors.shell);
for(const value of palette){const b=document.createElement('button');b.style.backgroundColor=value;b.title=value;b.setAttribute('aria-label','使用颜色 '+value);b.onclick=()=>{const target=el<HTMLSelectElement>('color-target').value;if(target==='all')for(const part of Object.keys(colors) as ColorPart[])setColor(part,value);else setColor(target as ColorPart,value);};el('palette').append(b);}
el('reset-colors').onclick=()=>{for(const part of Object.keys(colors) as ColorPart[])setColor(part,defaultColors[part]);};
for(const [value,label] of MOTIONS){const option=document.createElement('option');option.value=value;option.textContent=label;el('firmware-action').append(option);}
function playMotion(name:string){
 for(const id of ['firmware-period','firmware-steps','firmware-height']){const input=el<HTMLInputElement>(id);if(input.value===''||!input.reportValidity())return;}
 const params={period:Number(el<HTMLInputElement>('firmware-period').value),steps:Number(el<HTMLInputElement>('firmware-steps').value),height:Number(el<HTMLInputElement>('firmware-height').value),direction:Number(el<HTMLSelectElement>('firmware-direction').value)};
 motion=createMotion(name,params,pose);motionTime=0;action=name;el<HTMLSelectElement>('firmware-action').value=name;
 el('motion-spec').textContent=motion.spec?JSON.stringify({order:['左腿','右腿','左脚','右脚'],amplitude:motion.spec.A,offset:motion.spec.O,phaseRadians:motion.spec.P},null,2):name==='jump'?'[90,90,150,30] → [90,90,90,90]\n源码 Jump 不使用 steps 参数':'按源码 MoveServos 关键角度和停顿播放';
 document.querySelectorAll<HTMLElement>('[data-action]').forEach(b=>b.classList.toggle('selected',b.dataset.action===name));
}
el('play-firmware').onclick=()=>playMotion(el<HTMLSelectElement>('firmware-action').value);
