// HSV color wheel: hue around the circumference, saturation from the center.
export function colorWheel(host: HTMLElement, apply: (hex: string) => void) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 256;
  canvas.className = 'color-wheel'; canvas.tabIndex = 0;
  canvas.setAttribute('aria-label', '颜色圆盘；方向键调整色相和饱和度');
  const brightness = document.createElement('input');
  brightness.type = 'range'; brightness.min = '0'; brightness.max = '100'; brightness.value = '100';
  brightness.setAttribute('aria-label', '颜色亮度');
  const caption = document.createElement('label'); caption.className = 'range-field';
  caption.textContent = '亮度'; caption.append(brightness);
  host.append(canvas, caption);
  let hue = 45, saturation = .85, value = 1;
  const ctx = canvas.getContext('2d')!;
  function rgb(h: number, s: number, v: number) {
    const f = (n: number) => { const k = (n + h / 60) % 6; return Math.round(255 * v * (1 - s * Math.max(0, Math.min(k, 4-k, 1)))); };
    return [f(5), f(3), f(1)];
  }
  function draw() {
    const img = ctx.createImageData(256,256);
    for(let y=0;y<256;y++)for(let x=0;x<256;x++) {
      const dx=x-128,dy=y-128,r=Math.hypot(dx,dy)/124;
      if(r>1)continue;
      const c=rgb((Math.atan2(dy,dx)*180/Math.PI+360)%360,r,value), i=(y*256+x)*4;
      img.data.set([...c,255],i);
    }
    ctx.putImageData(img,0,0);
    const a=hue*Math.PI/180;
    ctx.beginPath();ctx.arc(128+Math.cos(a)*saturation*124,128+Math.sin(a)*saturation*124,6,0,Math.PI*2);
    ctx.strokeStyle='#000';ctx.lineWidth=4;ctx.stroke();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();
  }
  const commit=()=>{draw();apply('#'+rgb(hue,saturation,value).map(n=>n.toString(16).padStart(2,'0')).join(''));};
  const point=(e:PointerEvent)=>{const r=canvas.getBoundingClientRect(),x=(e.clientX-r.left)*256/r.width-128,y=(e.clientY-r.top)*256/r.height-128;hue=(Math.atan2(y,x)*180/Math.PI+360)%360;saturation=Math.min(1,Math.hypot(x,y)/124);commit();};
  canvas.onpointerdown=e=>{canvas.setPointerCapture(e.pointerId);point(e);};
  canvas.onpointermove=e=>{if(canvas.hasPointerCapture(e.pointerId))point(e);};
  canvas.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();hue=(hue+(e.key==='ArrowRight'?2:e.key==='ArrowLeft'?-2:0)+360)%360;saturation=Math.max(0,Math.min(1,saturation+(e.key==='ArrowUp'?.02:e.key==='ArrowDown'?-.02:0)));commit();};
  brightness.oninput=()=>{value=Number(brightness.value)/100;commit();};
  draw();
  return (hex: string) => {
    const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255);
    const max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;
    value=max;saturation=max?d/max:0;
    if(d)hue=((max===r?(g-b)/d:max===g?(b-r)/d+2:(r-g)/d+4)*60+360)%360;
    brightness.value=String(Math.round(value*100));draw();
  };
}
