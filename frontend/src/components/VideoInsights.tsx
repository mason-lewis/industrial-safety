import {useEffect,useState} from 'react';
import type {SceneReview} from '../types/contracts';
import type {Session} from '../types/api';
import type {AuditEvent} from '../api/generated';
import {request} from '../api/client';
import {AgentInsights,UsageMonitor} from './AgentInsights';
import {chatUsage,chronological} from './agentTrace';
import {reviewStatus} from './budgetInfo';
import {time} from './VideoView';
export default function VideoInsights({reviews,scene,session,inspectId,onError}:{reviews:SceneReview[];scene:unknown;session:Session|null;inspectId:string|null;onError:(e:string)=>void}){
 const candidate=(scene as {live_review?:SceneReview}|undefined)?.live_review;
 const active=candidate&&session&&candidate.session_id===(session.session_id??session.id)&&candidate.run_generation===session.run_generation?candidate:null;
 const [choice,setChoice]=useState(''),[history,setHistory]=useState<SceneReview[]>([]),[trace,setTrace]=useState<AuditEvent[]>([]),[view,setView]=useState<'tools'|'all'>('tools'),[loaded,setLoaded]=useState(false);
 useEffect(()=>{let valid=true;const load=()=>request<SceneReview[]>('/scene-reviews?limit=30').then(rows=>{if(valid)setHistory(rows);}).catch(e=>{if(valid)onError(String(e));});void load();const timer=setInterval(()=>void load(),2000);return()=>{valid=false;clearInterval(timer);};},[onError]);
 useEffect(()=>{setChoice('');},[session?.session_id,session?.run_generation]);
 useEffect(()=>{if(inspectId)setChoice(inspectId);},[inspectId]);
 const options=[...new Map([...history,...reviews,...(active?[active]:[])].map(r=>[r.id,r])).values()];
 const selected=choice?(options.find(r=>r.id===choice)??null):(active??reviews.at(-1)??history.at(-1)??null);
 const id=selected?.id;
 useEffect(()=>{setTrace([]);setLoaded(false);if(!id)return;let valid=true;const load=async()=>{try{const rows=await request<AuditEvent[]>(`/scene-reviews/${id}/audit?limit=1000`);if(valid){setTrace(rows);setLoaded(true);}}catch(e){if(valid)onError(String(e));}};void load();const timer=setInterval(()=>void load(),2000);return()=>{valid=false;clearInterval(timer);};},[id,onError]);
 const usage=chatUsage(trace);
 const activity=chronological(trace).filter(r=>view==='all'||r.event_type.startsWith('tool_')||r.event_type==='legacy_request_trace'||!!r.error);
 const finished=!!selected&&!['queued','running'].includes(selected.status??'queued')&&selected.id!==active?.id;
 return <div className="video-inspector"><div className="video-review-picker"><label>Inspect video review<select aria-label="Inspect video review" value={choice} onChange={e=>setChoice(e.target.value)}><option value="">Follow current / latest review</option>{options.map(r=><option key={r.id} value={r.id}>{time(r.packet.start_ms)}–{time(r.packet.cutoff_ms)} · {reviewStatus(r)}</option>)}</select></label>{selected&&<p className="muted">{time(selected.packet.start_ms)}–{time(selected.packet.cutoff_ms)} · {reviewStatus(selected)} · {String(selected.settings?.live_phase??selected.status).replaceAll('_',' ')}</p>}{selected?.error&&<p className="amber-text" role="alert">{selected.error}</p>}</div>
 <AgentInsights rows={trace} status={selected?reviewStatus(selected):'No review selected'} selected={!!id}/>
 <UsageMonitor title="Tokens & cost · this review" usage={usage} selected={!!id} fixture={selected?.settings?.billing_mode==='fixture'} partial={trace.length===1000}/>
 <section className="panel"><div className="panel-title"><h2>Requests, tools &amp; trace</h2><a className="chat-download" aria-disabled={!finished} href={finished?`/api/scene-reviews/${id}/audit/download`:undefined} download>Download audit</a></div><div className="toolbar"><button aria-pressed={view==='tools'} onClick={()=>setView('tools')}>Tools &amp; issues</button><button aria-pressed={view==='all'} onClick={()=>setView('all')}>All events</button></div><p className="muted">This review’s observable activity, inputs, results and errors. Hidden reasoning is not recorded.</p><div className="chat-trace">{activity.map(r=><details key={r.id}><summary><span className={`trace-dot ${r.error?'trace-error':''}`}/>{r.tool_name??r.event_type} · {r.status}</summary><p className="muted">{r.timestamp_utc} · {r.agent_name??'Coordinator'}</p><pre>{JSON.stringify(r,null,2)}</pre></details>)}{!activity.length&&<p className="muted">{id&&!loaded?'Loading audit…':'No matching audit events.'}</p>}</div></section>
 </div>;
}
