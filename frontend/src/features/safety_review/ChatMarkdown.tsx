import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

type Node={type:string;value?:string;children?:Node[];url?:string};
// Turn only known bare source citations into links; leave code and existing links intact.
function citations(refs:Record<string,string>){
 return ()=> (tree:Node)=>{
  function walk(node:Node){
   if(!node.children||['link','code','inlineCode'].includes(node.type))return;
   node.children=node.children.flatMap(child=>{
    if(child.type!=='text'||!child.value){walk(child);return [child];}
    const parts:Node[]=[];let start=0;
    for(const match of child.value.matchAll(/\[([^\]\n]+)\]/g)){
     const url=Object.prototype.hasOwnProperty.call(refs,match[1])?refs[match[1]]:undefined;if(typeof url!=='string')continue;
     if(match.index!>start)parts.push({type:'text',value:child.value.slice(start,match.index)});
     parts.push({type:'link',url,children:[{type:'text',value:match[0]}]});start=match.index!+match[0].length;
    }
    if(!parts.length)return [child];
    if(start<child.value.length)parts.push({type:'text',value:child.value.slice(start)});
    return parts;
   });
  }
  walk(tree);
 };
}
export default function ChatMarkdown({text,sourceLinks={}}:{text:string;sourceLinks?:Record<string,string>}){
 return <div className="chat-markdown"><Markdown remarkPlugins={[remarkGfm,citations(sourceLinks)]} skipHtml urlTransform={url=>/^(?:https?:\/\/|mailto:|#source-)/i.test(url)?url:''} components={{
  a:({href,children})=>href?<a href={href} target={href.startsWith('#')?undefined:'_blank'} rel={href.startsWith('#')?undefined:'noopener noreferrer'}>{children}</a>:<span>{children}</span>,
  img:({alt})=><span>{alt?`[Image: ${alt}]`:'[Image]'}</span>,
  table:({children})=><div className="chat-markdown-table"><table>{children}</table></div>,
 }}>{text}</Markdown></div>;
}
