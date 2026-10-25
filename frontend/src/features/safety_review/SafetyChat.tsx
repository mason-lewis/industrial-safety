import {useEffect,useRef,useState} from 'react';
import {request} from '../../api/client';
import type {AuditEvent,ChatSession,ChatState,ChatTurn,Job} from '../../api/generated';
import {agentActivity,chronological,chatUsage} from './chatTrace';
import './safetyChat.css';
import ChatMarkdown from './ChatMarkdown';
import {UsageMonitor} from '../../components/AgentInsights';

const pending=(job:Job)=>['queued','running'].includes(job.status??'queued');
export default function SafetyChat(){
 const [sessions,setSessions]=useState<ChatSession[]>([]),[sessionId,setSessionId]=useState(''),[state,setState]=useState<ChatState|null>(null);
 const [message,setMessage]=useState(''),[mode,setMode]=useState<'hosted'|'fixture'>('hosted'),[eventDate,setEventDate]=useState('');
 const [selected,setSelected]=useState(''),[trace,setTrace]=useState<AuditEvent[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[view,setView]=useState<'tools'|'all'>('tools');
 const retryKey=useRef<{signature:string;key:string}|null>(null);
 async function loadSessions(){const items=await request<ChatSession[]>('/chat/sessions');setSessions(items);setSessionId(old=>old||items[0]?.id||'');}
 useEffect(()=>{loadSessions().catch(e=>setError(String(e)));},[]);
 useEffect(()=>{
  setState(null);setTrace([]);setSelected('');
  if(!sessionId)return;
  let active=true;
  const load=async()=>{try{const next=await request<ChatState>(`/chat/sessions/${sessionId}`);if(active){setError('');setState(next);setSelected(old=>old||next.turns.at(-1)?.job.id||'');}}catch(e){if(active)setError(String(e));}};
  void load();const timer=setInterval(()=>void load(),2000);return()=>{active=false;clearInterval(timer);};
 },[sessionId]);
 useEffect(()=>{
  setTrace([]);if(!sessionId||!selected)return;
  let active=true;
  const load=async()=>{try{const rows=await request<AuditEvent[]>(`/chat/sessions/${sessionId}/turns/${selected}/audit?limit=1000`);if(active){setError('');setTrace(rows);}}catch(e){if(active)setError(String(e));}};
  void load();const timer=setInterval(()=>void load(),2000);return()=>{active=false;clearInterval(timer);};
 },[sessionId,selected]);
 const current=state?.turns.find(t=>t.job.id===selected);
 const hasPending=state?.turns.some(t=>pending(t.job));
 async function create(){setBusy(true);setError('');try{const s=await request<ChatSession>('/chat/sessions',{title:'Safety questions'});setSessions(old=>[s,...old]);setSessionId(s.id!);}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function send(){
  if(!sessionId||!message.trim()||hasPending)return;setBusy(true);setError('');
  const signature=JSON.stringify([sessionId,message,mode,eventDate]);
  if(retryKey.current?.signature!==signature)retryKey.current={signature,key:crypto.randomUUID()};
  try{const job=await request<Job>(`/chat/sessions/${sessionId}/messages`,{message,idempotency_key:retryKey.current!.key,mode,event_date:eventDate||null});setSelected(job.id!);setMessage('');retryKey.current=null;setState(await request<ChatState>(`/chat/sessions/${sessionId}`));}catch(e){setError(String(e));}finally{setBusy(false);}
 }
 async function cancel(){if(!current)return;try{await request(`/chat/sessions/${sessionId}/turns/${selected}/cancel`,{});setState(await request<ChatState>(`/chat/sessions/${sessionId}`));}catch(e){setError(String(e));}}
 const agents=agentActivity(trace),boss=agents.find(a=>a.name==='safety_reviewer'),others=agents.filter(a=>a.name.startsWith('specialist_')),challenger=agents.find(a=>a.name==='challenge_reviewer');
 const usage=chatUsage(trace);
 const activity=chronological(trace).filter(r=>view==='all'||r.event_type.startsWith('tool_')||r.event_type.startsWith('specialist_')||r.event_type==='source_packet_prepared'||r.error);
 return <main className="safety-chat"><header><div><span className="eyebrow">SAFETY TEAM</span><h1>Safety Chat</h1><p>Ask a safety question. Follow the boss, specialists, sources, and independent challenge.</p></div><button onClick={create} disabled={busy}>New chat</button></header>
 {error&&<p role="alert" className="amber-text">{error}</p>}
 <div className="chat-layout"><section className="panel chat-conversation"><div className="toolbar"><label>Conversation<select disabled={busy} value={sessionId} onChange={e=>setSessionId(e.target.value)}><option value="">Create a chat to begin</option>{sessions.map(s=><option key={s.id} value={s.id}>{s.title} · {new Date(s.created_at??'').toLocaleString()}</option>)}</select></label></div>
 <div className="chat-messages" aria-live="polite">{!state?.turns.length&&<div className="chat-empty"><h2>What would you like to check?</h2><p>Try “What facts should I check if someone walks under raised forklift forks?”</p><p className="muted">Federal sources are available. Described events remain unverified.</p><button onClick={()=>setMessage('What facts should I check if someone walks under raised forklift forks?')}>Use example question</button></div>}
 {state?.turns.map(turn=><ChatTurnView key={turn.job.id} turn={turn} selected={selected===turn.job.id} onSelect={()=>setSelected(turn.job.id!)}/>)}
 </div>
 <form onSubmit={e=>{e.preventDefault();void send();}} className="chat-composer"><label htmlFor="chat-question">Safety question<textarea id="chat-question" value={message} maxLength={1800} onChange={e=>setMessage(e.target.value)} placeholder="Describe the task, hazard, and facts you want to check…" disabled={busy}/></label><div className="toolbar"><label>Run mode<select value={mode} onChange={e=>setMode(e.target.value as 'hosted'|'fixture')}><option value="hosted">Safety team</option><option value="fixture">Local fixture · no paid calls</option></select></label><label>Event date (optional)<input type="date" value={eventDate} onChange={e=>setEventDate(e.target.value)}/></label><button className="primary" disabled={busy||!sessionId||!state||hasPending||!message.trim()} type="submit">{busy?'Submitting…':hasPending?'Team is working…':'Send question'}</button></div><p className="muted">{mode==='fixture'?'Fixture mode tests delegation and source tools with fixed demonstration answers.':'The review worker must be running. Existing paid-review settings and cost limits apply.'}</p></form>
 </section><aside className="chat-inspector"><section className="panel"><div className="panel-title"><h2>Team activity</h2><span>{current?.job.status??'No turn selected'}</span></div>{!selected?<p>Select a turn to inspect its activity.</p>:<><p className="muted">Turn {selected.slice(0,8)} · actual audit events</p>{current?.job.status==='queued'&&<p>Waiting for the review worker.</p>}<div className="team-tree" aria-label="Agent delegation activity">{boss&&<AgentNode item={boss}/>}<div className="team-branches">{others.map(a=><AgentNode key={a.id} item={a}/>)}</div>{challenger&&<div className="chat-challenger-node"><AgentNode item={challenger}/></div>}</div>{!agents.length&&<p>No agent has started for this turn.</p>}{current&&pending(current.job)&&<button onClick={cancel}>Cancel turn</button>}</>}</section>
 <UsageMonitor usage={usage} selected={Boolean(selected)} fixture={current?.job.payload.mode==='fixture'} partial={trace.length===1000}/><section className="panel"><div className="panel-title"><h2>Tool calls & trace</h2><a className="chat-download" aria-disabled={!current||pending(current.job)} href={current&&!pending(current.job)?`/api/chat/sessions/${sessionId}/turns/${selected}/audit/download`:undefined} download>Download audit</a></div><div className="toolbar"><button aria-pressed={view==='tools'} onClick={()=>setView('tools')}>Tools & issues</button><button aria-pressed={view==='all'} onClick={()=>setView('all')}>All events</button></div><p className="muted">Inputs, results, model usage, and errors are saved in the audit log.</p>{trace.length===1000&&<p>Showing the latest 1,000 records. Download the completed turn for the full trace.</p>}<div className="chat-trace">{activity.map((r,i)=><details key={r.id??i}><summary><span className={`trace-dot ${r.error?'trace-error':''}`}/>{r.tool_name??r.event_type}{r.event_type==='tool_started'?' · started':''} <span className="muted">{r.event_type==='tool_started'?'':'· '+r.status}</span></summary><p className="muted">#{r.sequence} · {r.agent_name??'Coordinator'} · {r.timestamp_utc}{r.duration_ms!=null?` · ${Math.round(r.duration_ms)} ms`:''}</p><pre>{JSON.stringify(r,null,2)}</pre></details>)}{!activity.length&&<p>No matching events yet.</p>}</div></section></aside></div></main>;
}
function AgentNode({item}:{item:ReturnType<typeof agentActivity>[number]}){return <div className={`agent-node agent-${item.status}`}><strong>{item.name==='safety_reviewer'?'Safety boss':item.name==='challenge_reviewer'?'Independent challenger':item.name.replace('specialist_','')+' specialist'}</strong><span className="badge blue">{item.status}</span><small>{item.requests} model requests · {item.tools} tools · {item.id.slice(0,8)}</small></div>;}
function ChatTurnView({turn,selected,onSelect}:{turn:ChatTurn;selected:boolean;onSelect:()=>void}){
 const {job,result}=turn,issues=result?.challenge.issues??[];
 const sourceLinks=Object.fromEntries((result?.citations??[]).map(c=>[c.id,`#source-${job.id}-${c.id}`]));
 return <article className={`chat-turn ${selected?'chat-turn-selected':''}`}><div className="chat-user"><strong>You</strong><ChatMarkdown text={String(job.payload.message)}/></div><div className="chat-assistant"><div className="panel-title"><strong>Safety team</strong><button onClick={onSelect} aria-pressed={selected}>Inspect turn · {job.status}</button></div>{job.payload.mode==='fixture'&&<span className="badge amber">Local fixture · demonstration only</span>}{job.error&&<><p role="alert" className="amber-text">{job.error.code}: {job.error.message}</p>{job.error.message.includes('Connection error')&&<p className="muted">The review worker could not connect to the model provider. After restoring worker network access, submit the question again to create a new turn. This failed turn and any unresolved charge remain in history.</p>}</>}{!result&&!job.error&&<p className="muted">{job.status==='queued'?'Queued. Waiting for the review worker.':job.status==='running'?'The team is reviewing your question…':`Turn ${job.status}. No completed answer.`}</p>}
 {result&&<>{result.response.completion==='partial'&&<p className="amber-text" role="status">Partial assessment: some specialist work could not finish within the request allowance. Review the missing facts and source limits.</p>}<ChatMarkdown text={result.response.answer} sourceLinks={sourceLinks}/>{(issues.length>0||(result.challenge.limitations??[]).length>0)&&<div className="chat-challenge"><strong>Independent challenge</strong>{issues.map((issue,i)=><div key={i}><small>{issue.claim_id}</small><ChatMarkdown text={issue.explanation} sourceLinks={sourceLinks}/></div>)}{result.challenge.limitations?.map((s,i)=><ChatMarkdown key={`limit-${i}`} text={s} sourceLinks={sourceLinks}/>)}</div>}
 {(result.response.claims??[]).length>0&&<details><summary>Claims & citations ({result.response.claims!.length})</summary>{result.response.claims!.map(c=><div key={c.id} className="chat-claim"><span className="badge blue">{issues.some(i=>i.claim_id===c.id)?'challenged':c.kind}</span><ChatMarkdown text={c.text} sourceLinks={sourceLinks}/><small>{c.id}{c.exact_clause?` · ${c.exact_clause}`:''}</small>{c.source_refs?.map(id=><a key={id} href={`#source-${job.id}-${id}`}>[{id}] </a>)}{(c.rule_refs??[]).length>0&&<p>Site rules: {c.rule_refs!.join(', ')}</p>}</div>)}</details>}
 {(result.response.missing_context??[]).length>0&&<details><summary>Missing facts & source limits ({result.response.missing_context!.length})</summary><ul>{result.response.missing_context!.map((s,i)=><li key={i}><ChatMarkdown text={s} sourceLinks={sourceLinks}/></li>)}</ul></details>}
 {result.citations.map(c=><details key={c.id} id={`source-${job.id}-${c.id}`}><summary>{c.title} · {c.clause??c.section}</summary><p className="muted">{c.source_type} · {c.jurisdiction} · stored version {c.version} · {c.id}</p><p className="muted">Effective date: {c.effective_date??'unverified'} · Context: {c.context_complete?'reviewed':'unverified'}</p><blockquote>{c.text}</blockquote>{c.url&&/^https?:\/\//.test(c.url)&&<a href={c.url} target="_blank" rel="noreferrer">Open original source</a>}</details>)}
 <details><summary>Specialist assessments ({result.delegations.length})</summary>{result.delegations.map((d,i)=><div key={i}><h3>{d.specialist} · {d.question_id} · {d.call_id}</h3><ChatMarkdown text={d.answer} sourceLinks={sourceLinks}/></div>)}</details></>}
 </div></article>;
}
