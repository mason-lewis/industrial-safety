import {useEffect,useState} from 'react';
import {request} from '../api/client';
import Icon from '../components/ReviewIcon';
import {useAiControl} from '../hooks/useAiControl';
export const screens=['Preview','Live','Review'] as const;
export type Screen=typeof screens[number];
export default function Navigation({screen,onSelect,onHome}:{screen:Screen,onSelect:(s:Screen)=>void,onHome:()=>void}){
 const ai=useAiControl();
 const [chatTesting,setChatTesting]=useState(false);
 useEffect(()=>{void request<{enabled:boolean}>('/chat-lab/capabilities').then(value=>setChatTesting(value.enabled)).catch(()=>{});},[]);
 return <nav className="consumer-nav" aria-label="Project modules">
  <button className="product-brand" onClick={onHome} aria-label="Industrial Safety home"><span className="brand-symbol"><Icon name="alert"/></span><span>Industrial<span className="brand-light"> Safety</span></span></button>
  <div className="mode-tabs">{screens.map(s=><button key={s} aria-current={screen===s?'page':undefined} onClick={()=>onSelect(s)}>{s==='Live'?<span className="tab-dot"/>:<Icon name={s==='Preview'?'eye':'video'}/>}{s}</button>)}</div>
  <div className="nav-actions">{chatTesting&&<a className="chat-testing-link" href="/test/chat">Chat Lab <small>Test</small></a>}<button className="ai-toggle" role="switch" aria-label="AI analysis" aria-checked={Boolean(ai.status?.enabled)} disabled={ai.busy||!ai.status||(!ai.status.enabled&&(!ai.status.hosted_allowed||!ai.status.key_configured))} onClick={()=>void ai.toggle()} title={ai.status?.message??ai.error??'Connecting to AI controls'}><span>{ai.busy?'Updating…':!ai.status?'AI connecting':ai.status.enabled?'AI on':ai.status.stopped?'AI off':'AI unavailable'}</span><span className="ai-toggle-track"><span/></span></button>
  </div>
 </nav>;
}
