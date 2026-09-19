export const LABELS:Record<string,string>={search_zhihu:'搜索知乎',search_global:'搜索全网',hot:'知乎热榜',answer:'知乎直答',recommend:'问题发现',answers:'问题回答摘要',contents:'我的帖子',detail:'创作全文',comments:'帖子评论',stats:'账号创作数据',content_stats:'单篇创作数据',followees:'我的关注',favorites:'近期收藏',favlists:'收藏夹',favlist_items:'收藏夹内容'};
export type Result={tool:string;title:string;args:Record<string,unknown>;data:any;source:string;retrieved_at:number;summary_only:boolean};
export const plain=(value:unknown)=>typeof value==='string'?value.replace(/<[^>]*>/g,''):String(value??'');
export function add(parent:HTMLElement,tag:string,text:unknown,cls=''){const n=document.createElement(tag);n.textContent=plain(text);n.className=cls;parent.append(n);return n;}
function link(parent:HTMLElement,text:string,href:unknown){try{const url=new URL(String(href));if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return;const a=add(parent,'a',text) as HTMLAnchorElement;a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';}catch{}}
export function renderResult(page:HTMLElement,result:Result,run:(tool:string,args:Record<string,unknown>)=>void){
 page.replaceChildren();page.scrollTop=0;
 const action=(parent:HTMLElement,label:string,tool:string,args:Record<string,unknown>)=>{const b=add(parent,'button',label) as HTMLButtonElement;b.type='button';b.onclick=()=>run(tool,args);};
 add(page,'h2',result.title);add(page,'p',`${result.source} · ${new Date(result.retrieved_at*1000).toLocaleTimeString()}`,'radio-web-notice');
 if(result.args.query)add(page,'p','主题：'+result.args.query);
 if(result.summary_only)add(page,'p','以下为列表或摘要，不是完整正文。','radio-web-notice');
 const data=result.data||{};
 if(data.Body!==undefined){add(page,'h3',data.Title);add(page,'p',data.Body,'radio-story');link(page,'原文',data.Url);}
 else if(data.Text!==undefined)add(page,'p',data.Text,'radio-story');
 else if(Array.isArray(data.Items)){
  if(!data.Items.length)add(page,'p',data.EmptyReason||'本页没有可显示的内容。');
  for(const item of data.Items){
   const card=add(page,'article','','radio-web-card');
   if(item.Comment){add(card,'p',item.Comment.Content);if(item.Comment.AuthorToken)add(card,'small','作者标识：'+item.Comment.AuthorToken);for(const child of item.Children||[])add(card,'blockquote',child.Content);continue;}
   add(card,'h3',item.Title||item.Fullname||'内容');const author=item.AuthorName||item.Author?.Name;if(author)add(card,'small',author);
   add(card,'p',item.ContentText??item.Summary??item.Description??item.Headline??'');link(card,'查看来源',item.Url);
   if(result.tool==='contents'&&item.ContentType!=='question'){action(card,'全文','detail',{url:item.Url});action(card,'评论','comments',{url:item.Url});action(card,'数据','content_stats',{url:item.Url});}
   if(result.tool==='recommend')action(card,'回答摘要','answers',{url:item.Url});
   if(result.tool==='favlists')action(card,'打开收藏夹','favlist_items',{folder_id:String(item.UrlToken)});
   if(item.Metrics)add(card,'pre',JSON.stringify(item.Metrics,null,2));
   for(const c of item.CommentInfoList||[])add(card,'blockquote',c.Content);
  }
 }else add(page,'pre',JSON.stringify(data,null,2));
 const paging=data.Paging;
 if(paging?.IsEnd===false){const next=String(paging.NextOffset??''),current=String(result.args.offset??'0');if(/^\d+$/.test(next)&&/^\d+$/.test(current)&&BigInt(next)>BigInt(current))action(page,'下一页',result.tool,{...result.args,offset:next});else add(page,'p','分页游标缺失或未前进，已停止翻页。');}
 if(result.tool==='favorites')add(page,'p','仅近期收藏，不代表完整历史。','radio-web-notice');
}
