import {CanvasTexture, SRGBColorSpace} from 'three';

export const EXAMPLE = `// 每帧调用：ctx 是 Canvas 2D，time 单位为秒
// width / height：屏幕尺寸；delta：帧间隔；state：持久状态
ctx.fillStyle = '#050810';
ctx.fillRect(0, 0, width, height);
const blink = Math.sin(time * 2) > 0.97 ? 3 : 20;
ctx.fillStyle = '#7df9ff';
for (const x of [width * 0.32, width * 0.68]) {
  ctx.beginPath();
  ctx.ellipse(x, height * 0.58, 15, blink, 0, 0, Math.PI * 2);
  ctx.fill();
}
ctx.strokeStyle = '#ffcf43';
ctx.lineWidth = 5;
ctx.beginPath();
ctx.arc(width / 2, height * 0.72, 20, 0, Math.PI);
ctx.stroke();`;

// User code runs in a Worker owned by an opaque-origin sandbox, never in the
// application realm. CSP denies network access. Parent enforces a frame deadline.
const WORKER = `
let draw, canvas, ctx, state = {};
onmessage = e => {
 try {
  if(e.data.type === 'init') {
   canvas = new OffscreenCanvas(240,240); ctx = canvas.getContext('2d');
   draw = new Function('ctx','width','height','time','delta','state', '"use strict";\\n'+e.data.code);
   postMessage({type:'ready'}); return;
  }
  if(e.data.type === 'tick' && draw) {
   ctx.save();
   try { draw(ctx,240,240,e.data.time,e.data.delta,state); } finally {ctx.restore();}
   const bitmap=canvas.transferToImageBitmap();postMessage({type:'frame',bitmap},[bitmap]);
  }
 } catch(error) { postMessage({type:'error',message:String(error)}); }
};`;

export class JsScreen {
 readonly canvas=document.createElement('canvas');
 readonly texture:CanvasTexture;
 private frame:HTMLIFrameElement|null=null;
 private pending=false;
 private ready=false;
 private deadline=0;
 private time=0;
 private timer:ReturnType<typeof setInterval>;
 constructor(private status:(text:string)=>void){
  this.canvas.width=this.canvas.height=240;
  this.texture=new CanvasTexture(this.canvas);this.texture.colorSpace=SRGBColorSpace;
  window.addEventListener('message',this.receive);
  this.timer=setInterval(()=>{if(this.pending && performance.now()>this.deadline){this.stop();this.status('已停止：代码超过 2 秒未返回，请检查死循环。');}},250);
 }
 private receive=(event:MessageEvent)=>{
  if(!this.frame || event.source!==this.frame.contentWindow)return;
  const data=event.data;
  if(data?.type==='ready'){this.ready=true;this.pending=false;this.status('JavaScript 动画运行中 · 240 × 240');}
  if(data?.type==='error'){this.stop();this.status('代码错误：'+String(data.message).slice(0,500));}
  if(data?.type==='frame' && data.bitmap instanceof ImageBitmap){
   if(data.bitmap.width===240 && data.bitmap.height===240){this.canvas.getContext('2d')!.drawImage(data.bitmap,0,0);this.texture.needsUpdate=true;}
   data.bitmap.close();this.pending=false;
  }
 };
 run(code:string){
  this.stop();if(code.length>100_000){this.status('代码过长，请限制在 100 KB 内');return;}
  this.time=0;const frame=document.createElement('iframe');frame.hidden=true;frame.sandbox.add('allow-scripts');frame.title='隔离的屏幕动画';
  frame.srcdoc=`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob:; worker-src blob:; connect-src 'none'"><script>
  const worker=new Worker(URL.createObjectURL(new Blob([${JSON.stringify(WORKER)}],{type:'text/javascript'})));
  worker.onmessage=e=>{if(e.data.type==='frame')parent.postMessage(e.data,'*',[e.data.bitmap]);else parent.postMessage(e.data,'*');};
  worker.onerror=e=>parent.postMessage({type:'error',message:e.message},'*');
  onmessage=e=>{if(e.source===parent)worker.postMessage(e.data);};
  <\/script>`;
  frame.onload=()=>{if(this.frame===frame)frame.contentWindow!.postMessage({type:'init',code},'*');};
  this.frame=frame;this.pending=true;this.deadline=performance.now()+2000;document.body.append(frame);this.status('正在编译 JavaScript…');
 }
 update(delta:number){
  if(this.ready)this.time+=delta;
  if(!this.ready||!this.frame||this.pending)return;
  this.pending=true;this.deadline=performance.now()+2000;
  this.frame.contentWindow!.postMessage({type:'tick',time:this.time,delta},'*');
 }
 stop(){this.frame?.remove();this.frame=null;this.ready=false;this.pending=false;}
 dispose(){this.stop();clearInterval(this.timer);window.removeEventListener('message',this.receive);this.texture.dispose();}
}
