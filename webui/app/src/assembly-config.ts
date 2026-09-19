/** Public otto.glb origins ×1000, ground shifted +89 on Y. */
export const PIVOTS = {ll:[25.5,49,0],rl:[-25.5,49,0],lf:[25.560148,17.616777,23.999993],rf:[-25.667563,17.483561,24.000015]} as const;
export const DEFAULT_ASSEMBLY = {shellHeight:51,shellDepth:0,shellYaw:0,bodyDepth:0};
export type AssemblyConfig = typeof DEFAULT_ASSEMBLY;
export const MOUNTS: {key:keyof AssemblyConfig;label:string;min:number;max:number;step?:number}[] = [
 {key:'shellHeight',label:'外壳底部高度',min:20,max:100},
 {key:'shellDepth',label:'外壳前后位置',min:-60,max:60},
 {key:'shellYaw',label:'外壳朝向',min:-180,max:180},
 {key:'bodyDepth',label:'机身前后微调',min:-20,max:20},
];
export const JOINTS = [{key:'ll',label:'左腿 · 垂直转轴'},{key:'rl',label:'右腿 · 垂直转轴'},{key:'lf',label:'左脚 · 踝关节'},{key:'rf',label:'右脚 · 踝关节'}] as const;
export type Joint = typeof JOINTS[number]['key'];
export type Pose = Record<Joint,number>;
export const HOME:Pose = {ll:90,rl:90,lf:90,rf:90};
