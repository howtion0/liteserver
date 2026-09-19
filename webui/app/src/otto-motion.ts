import {HOME,type Pose} from './assembly-config';
type Four=[number,number,number,number];
export type MotionParams={period:number;steps:number;height:number;direction:number};
export const MOTIONS=[['walk','行走'],['turn','转向'],['jump','跳跃'],['swing','左右摇摆'],['moonwalk','太空步'],['bend','弯曲'],['shake','摇腿'],['updown','上下运动'],['tiptoe','踮脚摇摆'],['jitter','抖动'],['ascending','升降转动'],['crusaito','交叉步'],['flapping','拍动']] as const;
const rad=(x:number)=>x*Math.PI/180;
const home:Four=[90,90,90,90];
const arr=(p:Pose):Four=>[p.ll,p.rl,p.lf,p.rf];
const pose=(v:number[]):Pose=>({ll:v[0],rl:v[1],lf:v[2],rf:v[3]});
const clamp=(x:number)=>Math.max(0,Math.min(180,x));
// std::round rounds negative half values away from zero, unlike Math.round.
const round=(x:number)=>Math.sign(x)*Math.round(Math.abs(x));
export function oscillatorSpec(name:string,h:number,d:number):{A:Four;O:Four;P:Four}{
 let A:Four=[0,0,0,0],O:Four=[0,0,0,0],P:Four=[0,0,0,0];const half=Math.trunc(h/2);
 switch(name){
 case 'walk':A=[30,30,30,30];O=[0,0,5,-5];P=[0,0,rad(-90*d),rad(-90*d)];break;
 case 'turn':A=d===1?[30,0,30,30]:[0,30,30,30];O=[0,0,5,-5];P=[0,0,rad(-90),rad(-90)];break;
 case 'swing':A=[0,0,h,h];O=[0,0,half,-half];break;
 case 'tiptoe':A=[0,0,h,h];O=[0,0,h,-h];break;
 case 'updown':A=[0,0,h,h];O=[0,0,h,-h];P=[0,0,rad(-90),rad(90)];break;
 case 'jitter':h=Math.min(25,h);A=[h,h,0,0];P=[rad(-90),rad(90),0,0];break;
 case 'ascending':h=Math.min(13,h);A=[h,h,h,h];O=[0,0,h+4,-h+4];P=[rad(-90),rad(90),rad(-90),rad(90)];break;
 case 'moonwalk':A=[0,0,h,h];O=[0,0,half+2,-half-2];P=[0,0,rad(-90*d),rad(-150*d)];break;
 // Source literally uses 90 radians for the hips; do not silently fix it.
 case 'crusaito':A=[25,25,h,h];O=[0,0,half+4,-half-4];P=[90,90,0,rad(-60*d)];break;
 case 'flapping':A=[12,12,h,h];O=[0,0,h-10,-h+10];P=[0,rad(180),rad(-90*d),rad(90*d)];break;
 default:throw new Error('未知振荡动作 '+name);
 }return {A,O,P};
}
/** Port of the four-leg/foot channels in OttoMovements. Logical angle preview:
 * no NVS trims, physical servo slew limiting, scheduler jitter or rigid-body physics.
 * Each preview starts oscillator phase at zero for reproducible inspection. */
export function createMotion(name:string,p:MotionParams,initial:Pose={...HOME}){
 const T=Math.max(500,Math.min(1500,p.period)),steps=Math.max(1,Math.min(100,Math.trunc(p.steps))),h=Math.max(0,Math.min(170,Math.trunc(p.height))),d=p.direction===-1?-1:1;
 const segments:{duration:number;from:Four;to:Four}[]=[];let current=arr(initial);
 const move=(duration:number,to:Four)=>{segments.push({duration,from:current,to});current=to;};
 const hold=(duration:number)=>move(duration,current);
 let spec:ReturnType<typeof oscillatorSpec>|null=null;let actionDuration=0;
 if(name==='jump'){move(T,[90,90,150,30]);move(T,home);} // Source Jump ignores steps.
 else if(name==='bend')for(let i=0;i<steps;i++){move(400,d===1?[90,90,62,35]:[90,90,145,120]);move(400,d===1?[90,90,62,105]:[90,90,75,120]);hold(T*.8);move(500,home);}
 else if(name==='shake'){
  const Q=Math.max(T-1000,400);const a:Four=d===1?[90,90,145,122]:[90,90,58,35],b:Four=d===1?[90,90,60,122]:[90,90,58,120],c:Four=d===1?[90,90,120,122]:[90,90,58,60];
  for(let i=0;i<steps;i++){move(500,a);move(500,b);for(let j=0;j<2;j++){move(Q/4,c);move(Q/4,b);}move(500,home);}hold(Q);
 }else{spec=oscillatorSpec(name,h,d);actionDuration=T*steps;}
 if(!spec)actionDuration=segments.reduce((s,x)=>s+x.duration,0);
 const rawAt=(ms:number):Four=>{
  if(spec){const phase=2*Math.PI*ms/T;return spec.A.map((a,i)=>clamp(90+round(a*Math.sin(phase+spec!.P[i])+spec!.O[i]))) as Four;}
  let t=ms;for(const s of segments){if(t<=s.duration)return s.from.map((x,i)=>clamp(x+(s.to[i]-x)*Math.max(0,t)/s.duration)) as Four;t-=s.duration;}return current;
 };
 const last=rawAt(actionDuration);
 return {duration:actionDuration+700,spec,sample(ms:number):Pose{
  if(ms<actionDuration)return pose(rawAt(Math.max(0,ms)));
  const progress=Math.min(1,(ms-actionDuration)/500);return pose(last.map((x,i)=>x+(home[i]-x)*progress));
 }};
}
