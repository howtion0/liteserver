import * as T from 'three';
import {STLLoader} from 'three/addons/loaders/STLLoader.js';
import {GIFEncoder,quantize,applyPalette} from 'gifenc';
const button=document.querySelector<HTMLButtonElement>('#generate')!;
const status=document.querySelector('#status')!;
button.onclick=async()=>{
 button.disabled=true;
 try{
 const size=512,frames=120;
 const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
 renderer.setSize(size,size);renderer.setPixelRatio(1);renderer.setClearColor('#081b64');
 renderer.toneMapping=T.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
 document.body.append(renderer.domElement);
 const scene=new T.Scene(), camera=new T.PerspectiveCamera(35,1,.01,100);
 scene.add(new T.HemisphereLight(0xffffff,0x8d9199,2));
 const key=new T.DirectionalLight(0xffffff,3);key.position.set(3,5,6);scene.add(key);
 const fill=new T.DirectionalLight(0xffffff,1.4);fill.position.set(-4,2,-3);scene.add(fill);
 const flat=document.createElement('canvas');flat.width=flat.height=size;const ctx=flat.getContext('2d',{willReadFrequently:true})!;
 for(const [id,name,color] of [['a','刘看山机器人-白色旋转','#ffffff'],['b','奶蛙机器人-黄色旋转','#ffd21c']]){
   const geometry=await new STLLoader().loadAsync('/models/nailong-'+id+'.stl');
   geometry.rotateX(-Math.PI/2);geometry.computeBoundingBox();
   const box=geometry.boundingBox!, center=box.getCenter(new T.Vector3()),dimensions=box.getSize(new T.Vector3());
   geometry.translate(-center.x,-center.y,-center.z);geometry.scale(...new Array(3).fill(2.9/Math.max(dimensions.x,dimensions.y,dimensions.z)) as [number,number,number]);geometry.computeBoundingSphere();
   const mesh=new T.Mesh(geometry,new T.MeshStandardMaterial({color,roughness:.57,metalness:0,side:T.DoubleSide}));scene.add(mesh);
   const distance=geometry.boundingSphere!.radius/Math.sin(T.MathUtils.degToRad(17.5))*1.08;
   camera.position.copy(new T.Vector3(.65,.35,1.6).normalize().multiplyScalar(distance));camera.lookAt(0,0,0);
   const gif=GIFEncoder();
   for(let frame=0;frame<frames;frame++){
     mesh.rotation.y=frame/frames*Math.PI*2;renderer.render(scene,camera);
     ctx.drawImage(renderer.domElement,0,0);const rgba=ctx.getImageData(0,0,size,size).data;
     const palette=quantize(rgba,256);gif.writeFrame(applyPalette(rgba,palette),size,size,{palette,delay:50,repeat:0});
     if(frame%5===0){status.textContent=`${name} ${frame+1}/${frames}`;await new Promise(resolve=>setTimeout(resolve,0));}
   }
   gif.finish();const bytes=gif.bytes();let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
   const href='data:image/gif;base64,'+btoa(binary),a=document.createElement('a');a.id='download-'+id;a.href=href;a.download=name+'.gif';a.textContent=name+' 下载';
   const image=document.createElement('img');image.src=href;image.width=384;image.alt=name;const section=document.createElement('section');section.append(a,image);document.querySelector('#results')!.append(section);
   scene.remove(mesh);geometry.dispose();mesh.material.dispose();
 }
 renderer.dispose();renderer.domElement.remove();status.textContent='两个 GIF 已生成';
 }catch(error){status.textContent=String(error);console.error(error);button.disabled=false;}
};
