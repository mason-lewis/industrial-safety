import {useEffect,useId,useRef,useState,type ReactNode} from 'react';
import {createPortal} from 'react-dom';

/** A hoverable tooltip that also opens with keyboard focus or a tap. */
export default function HelpTip({label,children}:{label:string;children:ReactNode}){
 const id=useId(),trigger=useRef<HTMLButtonElement>(null),tip=useRef<HTMLDivElement>(null);
 const [open,setOpen]=useState(false),[position,setPosition]=useState({left:12,top:12});
 const timer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const cancel=()=>clearTimeout(timer.current);
 const show=()=>{cancel();setOpen(true);};
 const leave=()=>{cancel();timer.current=setTimeout(()=>{if(document.activeElement!==trigger.current)setOpen(false);},150);};
 useEffect(()=>()=>clearTimeout(timer.current),[]);
 useEffect(()=>{
  if(!open)return;
  const place=()=>{const box=trigger.current?.getBoundingClientRect();if(!box)return;const width=Math.min(340,window.innerWidth-24),height=tip.current?.offsetHeight??180;setPosition({left:Math.max(12,Math.min(box.right-width,window.innerWidth-width-12)),top:box.bottom+height+20<window.innerHeight?box.bottom+8:Math.max(12,box.top-height-8)});};
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){cancel();setOpen(false);e.stopPropagation();}};
  const outside=(e:PointerEvent)=>{if(!trigger.current?.contains(e.target as Node)&&!tip.current?.contains(e.target as Node))setOpen(false);};
  place();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);document.addEventListener('keydown',key,true);document.addEventListener('pointerdown',outside);
  return()=>{window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);document.removeEventListener('keydown',key,true);document.removeEventListener('pointerdown',outside);};
 },[open]);
 return <><button type="button" ref={trigger} className="help-trigger" aria-label={`Help: ${label}`} aria-describedby={open?id:undefined} onMouseEnter={show} onMouseLeave={leave} onFocus={show} onBlur={()=>{cancel();setOpen(false);}} onClick={show}>?<span className="sr-only"> Help</span></button>{open&&createPortal(<div ref={tip} id={id} role="tooltip" className="help-tooltip" style={position} onMouseEnter={cancel} onMouseLeave={leave}><strong>{label}</strong><p>{children}</p><small>Esc to dismiss</small></div>,document.fullscreenElement??document.body)}</>;
}
export function HelpSetting({label,help,children}:{label:string;help:string;children:ReactNode}){
 return <div className="help-setting">{children}<HelpTip label={label}>{help}</HelpTip></div>;
}
