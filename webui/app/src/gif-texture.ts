import {CanvasTexture, SRGBColorSpace} from 'three';
import {parseGIF, decompressFrames} from 'gifuct-js';
/** Composite GIF patches and disposal operations into a WebGL canvas texture. */
export class GifTexture {
 readonly canvas=document.createElement('canvas'); readonly texture=new CanvasTexture(this.canvas);
 private frames:{image:ImageData;delay:number}[]=[];private index=0;private elapsed=0;private revision=0;
 constructor(){this.texture.colorSpace=SRGBColorSpace;}
 async load(url:string){
  const revision=++this.revision;const response=await fetch(url);if(!response.ok)throw new Error(`GIF HTTP ${response.status}`);
  const data=parseGIF(await response.arrayBuffer());
  if(data.lsd.width*data.lsd.height>4_000_000)throw new Error('GIF 尺寸过大');
  const decoded=decompressFrames(data,true);
  if(decoded.length*data.lsd.width*data.lsd.height>50_000_000)throw new Error('GIF 帧数据过大');
  const buffer=document.createElement('canvas');buffer.width=data.lsd.width;buffer.height=data.lsd.height;
  const ctx=buffer.getContext('2d')!;const patch=document.createElement('canvas');const frames:{image:ImageData;delay:number}[]=[];
  for(const f of decoded){
   const before=f.disposalType===3?ctx.getImageData(0,0,buffer.width,buffer.height):null;
   patch.width=f.dims.width;patch.height=f.dims.height;
   patch.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(f.patch),f.dims.width,f.dims.height),0,0);ctx.drawImage(patch,f.dims.left,f.dims.top);
   frames.push({image:ctx.getImageData(0,0,buffer.width,buffer.height),delay:Math.max(f.delay,20)});
   if(f.disposalType===2)ctx.clearRect(f.dims.left,f.dims.top,f.dims.width,f.dims.height);if(before)ctx.putImageData(before,0,0);
  }
  if(revision!==this.revision)return;if(!frames.length)throw new Error('GIF 没有动画帧');
  this.texture.dispose();this.canvas.width=buffer.width;this.canvas.height=buffer.height;this.frames=frames;this.index=0;this.elapsed=0;this.paint();
 }
 private paint(){this.canvas.getContext('2d')!.putImageData(this.frames[this.index].image,0,0);this.texture.needsUpdate=true;}
 update(dt:number){if(!this.frames.length)return;this.elapsed+=dt*1000;let changed=false;while(this.elapsed>=this.frames[this.index].delay){this.elapsed-=this.frames[this.index].delay;this.index=(this.index+1)%this.frames.length;changed=true;}if(changed)this.paint();}
 get frame(){return this.index;} get frameCount(){return this.frames.length;}
}
