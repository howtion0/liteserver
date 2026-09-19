const TOKEN_KEY='otto-console-token';
let authRejected=false;

export class ApiError extends Error{
 constructor(message:string,readonly status:number,readonly code:string){super(message);this.name='ApiError';}
}

export function consoleToken(){return window.sessionStorage.getItem(TOKEN_KEY)||'';}
export function hasConsoleAuthorization(){return Boolean(consoleToken())&&!authRejected;}
export function setConsoleToken(value:string){
 const normalized=value.trim();
 if(normalized)window.sessionStorage.setItem(TOKEN_KEY,normalized);else window.sessionStorage.removeItem(TOKEN_KEY);
 authRejected=false;window.dispatchEvent(new CustomEvent('otto-auth-change'));
}

export function bootstrapLoopbackToken(){
 const loopback=new Set(['127.0.0.1','localhost','::1','[::1]']);
 const fragment=new URLSearchParams(window.location.hash.replace(/^#/,''));
 const value=fragment.get('console_token');
 if(!value||!loopback.has(window.location.hostname))return false;
 setConsoleToken(value);
 window.history.replaceState(null,'',window.location.pathname+window.location.search);
 return true;
}

bootstrapLoopbackToken();

export async function api<T=unknown>(path:string,method='GET',body?:unknown):Promise<T>{
 const controller=new AbortController(),timer=window.setTimeout(()=>controller.abort(),70000);
 const headers=new Headers({'Accept':'application/json'}),token=consoleToken();
 if(token)headers.set('Authorization',`Bearer ${token}`);
 if(body!==undefined)headers.set('Content-Type','application/json');
 try{
  const response=await fetch('/api'+path,{method,signal:controller.signal,headers,cache:'no-store',credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json().catch(()=>({})) as {error?:{code?:string;message?:string};message?:string};
  if(!response.ok){
   if(response.status===401){authRejected=true;window.dispatchEvent(new CustomEvent('otto-auth-change'));}
   const code=data.error?.code||String(response.status),message=data.error?.message||data.message||response.statusText||'请求失败';
   throw new ApiError(`${code}: ${message}`,response.status,code);
  }
  if(token&&authRejected){authRejected=false;window.dispatchEvent(new CustomEvent('otto-auth-change'));}
  return data as T;
 }catch(error){
  if(error instanceof DOMException&&error.name==='AbortError')throw new ApiError('请求超时，未自动重试',408,'request_timeout');
  throw error;
 }finally{window.clearTimeout(timer);}
}
