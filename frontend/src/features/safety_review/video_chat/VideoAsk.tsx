import {useEffect,useRef,useState} from 'react';
import type {LabTelemetry,VideoChatFocus,VideoChatTurn,VideoEvidenceRef,PersonFrame} from '../../../api/generated';
import Dialog from '../../../components/ReviewDialog';
import Icon from '../../../components/ReviewIcon';
import {formatTime} from '../../../components/reviewPresentation';
import ChatMarkdown from '../ChatMarkdown';
import AgentOrchestration from '../../../components/AgentOrchestration';
import {buildAskOrchestration} from '../../../components/orchestration';
import {busyTurn,useVideoChat} from './useVideoChat';
import './videoAsk.css';

const labels:Record<string,string>={visual:'Observed in frames',record:'Saved review',guidance:'Source-backed guidance',requirement:'Legal reference',general:'General guidance',uncertain:'Uncertain',product:'Product documentation'};
const apiName=(api:string)=>api==='decisions'?'Luna · Decisions':api==='web_search'?'OpenAI web search':api==='local'?'Local tools':'Luna · Responses';
const dollars=(n=0)=>`$${n.toFixed(4)}`;
const seconds=(n=0)=>`${(n/1000).toFixed(1)}s`;
const frameUrl=(run:string,turn:string,frame:string)=>`/api/review-runs/${run}/chat/turns/${turn}/frames/${encodeURIComponent(frame)}`;
export function progressStages(events:LabTelemetry[]){
 const groups=[['Find evidence',/scope|route|find evidence|lookup|coordination|choose relevant/i],['Inspect',/inspect moment|inspect frames/i],['Check sources',/sources|source discovery|applicability|specialist|explain the evidence/i],['Verify',/verif|challenge|correct|recheck|final scope/i]] as const;
 return groups.filter(([,pattern])=>events.some(e=>e.event_type==='agent_started'&&pattern.test(e.agent??''))).map(([label,pattern])=>{
  const starts=events.filter(e=>e.event_type==='agent_started'&&pattern.test(e.agent??''));
  const done=starts.every(s=>events.some(e=>e.node_id===s.node_id&&e.event_type==='agent_finished'));
  return {label,done};
 });
}
function Activity({turn,events,onOpen}:{turn:VideoChatTurn;events:LabTelemetry[];onOpen:()=>void}){
 const [now,setNow]=useState(Date.now());useEffect(()=>{if(!busyTurn(turn))return;const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id);},[turn.job.status]);
 const nodes=events.filter(e=>e.event_type==='agent_started');
 return <details className="ask-activity" open={busyTurn(turn)||undefined} onToggle={e=>{if(e.currentTarget.open)onOpen();}}><summary><Icon name="eye"/>Activity <span>{turn.usage?.requests??0} requests · {dollars(turn.usage?.calculated_usd)}</span></summary><div className="ask-activity-body">
  <AgentOrchestration orchestration={buildAskOrchestration(events,now,!busyTurn(turn))} now={now} title="Agent team" empty="Waiting for a worker."/>
  <details className="ask-steps"><summary>Steps & tools</summary><ol className="ask-team-flow">{nodes.map((node,index)=>{
   const rows=events.filter(e=>e.node_id===node.node_id),end=rows.find(e=>e.event_type==='agent_finished');
   const waiting=rows.some(e=>e.event_type==='request_queued')&&!rows.some(e=>e.event_type==='request_queue_finished');
   const status=end?.state??(busyTurn(turn)?waiting?'queued':'running':turn.job.status==='cancelled'?'cancelled':'failed');
   const usage=rows.filter(e=>e.event_type==='model_response').reduce((sum,e)=>sum+Number(e.resources?.usage_cost_usd??0),0);
   const duration=end?.duration_ms??Math.max(0,now-Date.parse(node.timestamp_utc));
   const queue=rows.filter(e=>e.event_type==='request_queue_finished').reduce((sum,e)=>sum+(e.duration_ms??0),0)+(waiting?Math.max(0,now-Date.parse(rows.find(e=>e.event_type==='request_queued')!.timestamp_utc)):0);
   const handoff=rows.find(e=>e.event_type==='handoff');
   return <li key={node.node_id} className={`ask-node ${status}`}><span className="ask-node-dot">{index+1}</span><details><summary><span><strong>{node.agent}</strong><small>{apiName(node.api??'responses')} · {status==='queued'?'Waiting':status}</small></span><span>{seconds(Math.max(0,duration-queue))}<small>{dollars(usage)}{queue>0?` · ${seconds(queue)} waiting`:null}</small></span></summary><div>
    {handoff&&<p className="ask-caption">{index?'Evidence handed over from '+nodes[index-1].agent:'Question assigned to the team'}</p>}
    {rows.filter(e=>e.event_type==='tool_started'||e.event_type==='native_tool_started').map(tool=>{const finish=rows.find(e=>e.tool_call_id===tool.tool_call_id&&['tool_finished','native_tool_finished'].includes(e.event_type));return <details className="ask-tool" key={tool.sequence}><summary>{tool.tool} <small>{finish?seconds(finish.duration_ms??0):busyTurn(turn)?'Working…':'Interrupted'}</small></summary><pre>{JSON.stringify({arguments:tool.arguments,result:finish?.result},null,2)}</pre></details>;})}
    <details className="ask-tool"><summary>Assignment & usage</summary><pre>{JSON.stringify({assignment:handoff?.arguments,events:rows.filter(e=>['model_response','request_queue_finished','token_preflight_completed','recovery_started'].includes(e.event_type)).map(e=>({event:e.event_type,duration_ms:e.duration_ms,...e.resources}))},null,2)}</pre></details>
   </div></details></li>;
  })}</ol></details>
  <dl className="ask-cost-grid"><div><dt>Execution</dt><dd>{seconds(turn.execution_ms)}</dd></div><div><dt>Queue</dt><dd>{seconds(turn.queue_ms)}</dd></div><div><dt>Calculated</dt><dd>{dollars(turn.usage?.calculated_usd)}</dd></div><div><dt>Reserved</dt><dd>{dollars(turn.usage?.reserved_usd)}</dd></div><div><dt>Unknown charges</dt><dd>{turn.usage?.unknown_requests??0}</dd></div><div><dt>Correction</dt><dd>{turn.recovery?.attempts??0} · {dollars(turn.recovery?.calculated_usd)}</dd></div></dl>
  <details><summary>Technical details & raw events</summary><pre>{JSON.stringify({usage:turn.usage,issues:turn.issue_codes,versions:turn.versions,events},null,2)}</pre></details>
 </div></details>;
}
export default function VideoAsk({runId,mode,focus,onClearFocus,onSeek,focusToken=0}:{runId:string;mode:'fixture'|'hosted';focus:VideoChatFocus;onClearFocus:()=>void;onSeek:(ms:number)=>void;focusToken?:number}){
 const chat=useVideoChat(runId),[text,setText]=useState(''),[view,setView]=useState<{turn:VideoChatTurn;evidence:VideoEvidenceRef}|null>(null);
 const composer=useRef<HTMLTextAreaElement>(null),tail=useRef<HTMLDivElement>(null);
 useEffect(()=>{if(focusToken){composer.current?.focus();if(focus.finding_id)setText('Explain this finding.');}},[focusToken]);
 useEffect(()=>{tail.current?.scrollIntoView({block:'nearest',behavior:'instant'});},[chat.state?.turns?.length,chat.pending]);
 const suggestions=['Summarize the reviewed footage','What is visible here?','Explain this finding'];
 const activeEvents=chat.active?chat.events[chat.active.id]??[]:[];
 const activeStep=[...activeEvents].reverse().find(e=>e.event_type==='agent_started'&&!activeEvents.some(f=>f.node_id===e.node_id&&f.event_type==='agent_finished'));
 const waiting=activeEvents.some(e=>e.event_type==='request_queued'&&!activeEvents.some(f=>f.node_id===e.node_id&&f.sequence>e.sequence&&f.event_type==='request_queue_finished'));
 const showEvidence=(turn:VideoChatTurn,evidence:VideoEvidenceRef)=>{onSeek(evidence.frame.timestamp_ms);setView({turn,evidence});};
 return <section className="video-ask" aria-label="Ask about this review">
  <header className="ask-heading"><span className="ask-mark"><Icon name="eye"/></span><div><h2>Ask</h2><p>About any moment or finding.</p></div></header>
  <div className="ask-conversation">
   {!chat.state&&!chat.error&&<p role="status">Opening your conversation…</p>}
   {chat.state&&!chat.state.turns?.length&&!chat.pending&&<div className="ask-empty"><h3>Try one</h3><div className="ask-suggestions">{suggestions.map(s=><button type="button" key={s} onClick={()=>{setText(s);composer.current?.focus();}}>{s}<Icon name="arrow"/></button>)}</div><small>Visual samples only. Audio is not assessed. This is assisted review, not validated hazard detection.</small></div>}
   {chat.state?.turns?.map(turn=><article className="ask-turn" key={turn.id}>
    <div className="ask-question"><span>You{turn.context.focus.timestamp_ms!=null?` · ${formatTime(turn.context.focus.timestamp_ms,true)}`:turn.context.focus.finding_id?' · Selected finding':''}</span><p>{turn.message}</p></div>
    {turn.mode==='fixture'&&<span className="ask-fixture">Demo response · no paid requests</span>}
    {turn.answer?<div className="ask-answer">
     {turn.answer.claims?.length?turn.answer.claims.map(claim=><section className={`ask-block evidence-${claim.kind}`} key={claim.id}><span className="ask-evidence-label">{labels[claim.kind]}</span><ChatMarkdown text={claim.text}/><div className="ask-citations">{claim.citation_ids?.map(id=>{const frame=turn.evidence?.find(e=>e.id===id),source=turn.citations?.find(c=>c.id===id);return frame?<button className="ask-frame" key={id} onClick={()=>showEvidence(turn,frame)}><img loading="lazy" src={frameUrl(runId,turn.id,id)} alt={`Evidence at ${formatTime(frame.frame.timestamp_ms,true)}`}/><span><Icon name="play"/>{formatTime(frame.frame.timestamp_ms,true)}{frame.origin==='crop'?' · Detail':''}</span></button>:source?<details className="ask-source" key={id}><summary>{source.title}</summary><p>{source.text}</p>{source.url&&/^https:\/\//.test(source.url)&&<a target="_blank" rel="noreferrer" href={source.url}>Original source</a>}{Array.isArray(source.provenance?.sources)&&(source.provenance.sources as {url:string;title:string}[]).filter(link=>/^https:\/\//.test(link.url)).map(link=><p key={link.url}><a target="_blank" rel="noreferrer" href={link.url}>{link.title}</a></p>)}<small>{source.retrieved_at?`Retrieved ${source.retrieved_at}`:''} · {source.version}</small></details>:null;})}</div></section>):<ChatMarkdown text={turn.answer.answer}/>}
     {!!turn.answer.missing_context?.length&&<details className="ask-limitations"><summary>What remains uncertain ({turn.answer.missing_context.length})</summary><ol>{turn.answer.missing_context.slice(0,5).map((gap,i)=><li key={i}>{gap}</li>)}</ol>{turn.answer.missing_context.length>5&&<details><summary>More limitations</summary><ol start={6}>{turn.answer.missing_context.slice(5).map((gap,i)=><li key={i}>{gap}</li>)}</ol></details>}</details>}
    </div>:busyTurn(turn)?<div className="ask-progress" role="status"><p><span className="ask-live-dot"/>{!chat.connected?'Reconnecting to activity…':waiting?'Waiting for the team':activeStep?.agent??(turn.job.status==='queued'&&!activeEvents.some(e=>e.event_type==='agent_started')?'Waiting for the review worker':'Finishing checks')}</p><ol>{progressStages(chat.events[turn.id]??[]).map(stage=><li className={stage.done?'done':''} key={stage.label}>{stage.done?<Icon name="check"/>:<span className="ask-live-dot"/>}{stage.label}</li>)}</ol></div>:<p>This question did not finish. Open Activity for details.</p>}
    <Activity turn={turn} events={chat.events[turn.id]??[]} onOpen={()=>void chat.activity(turn.id)}/>
   </article>)}
   {chat.pending&&<div className="ask-question"><span>You · {chat.sending?'Sending…':'Submission not confirmed'}</span><p>{chat.pending.message}</p></div>}
   <div ref={tail}/>
  </div>
  {chat.error&&<div className="ask-error" role="alert">{chat.error}{chat.technical&&<details><summary>Technical details</summary><pre>{chat.technical}</pre></details>}{chat.pending&&<button disabled={chat.sending||!!chat.active} onClick={()=>void chat.send(chat.pending!)}>Retry saved submission</button>}</div>}
  <form className="ask-composer" onSubmit={async e=>{e.preventDefault();if(!text.trim()||chat.pending)return;const body={message:text.trim(),idempotency_key:crypto.randomUUID(),mode,focus};if(await chat.send(body)){setText('');onClearFocus();composer.current?.focus();}}}>
   {(focus.timestamp_ms!=null||focus.finding_id)&&<button type="button" className="ask-focus-chip" onClick={onClearFocus} aria-label="Remove question focus"><Icon name="video"/>{focus.finding_id?'Selected finding':formatTime(focus.timestamp_ms!,true)}<Icon name="close"/></button>}
   <label className="sr-only" htmlFor={`ask-${runId}`}>Question about this video</label><textarea ref={composer} id={`ask-${runId}`} value={text} onChange={e=>setText(e.target.value)} placeholder="Ask about this video…" maxLength={4000} rows={3}/>
   <div className="ask-send-row"><span>{mode==='fixture'?'Demo · no charge':'Up to $0.05 per question'}</span>{chat.active?<button type="button" className="ask-stop" onClick={()=>void chat.stop(chat.active!.id)}><Icon name="stop"/>Stop</button>:<button className="solid-button" type="submit" disabled={!text.trim()||chat.sending||!!chat.pending||!chat.state||mode==='hosted'&&!chat.state.hosted_available}>Send <Icon name="arrow"/></button>}</div>
   {mode==='hosted'&&chat.state&&!chat.state.hosted_available&&<p className="ask-caption">AI is unavailable. Saved conversations and evidence remain accessible.</p>}
   <details className="ask-privacy"><summary>About this review</summary><p>Footage stays on this device. Sending a question may share selected frames and relevant review context with OpenAI. Audio is excluded. Provider processing can retain data under its applicable data controls; store=false does not promise zero retention. Video details are excluded from web-search queries.</p><a href="https://developers.openai.com/api/docs/guides/your-data" target="_blank" rel="noreferrer">Provider data controls</a> · <a href={`/api/review-runs/${runId}/chat/export`}>Export conversation</a></details>
  </form>
  {view&&<Dialog title={`Evidence · ${formatTime(view.evidence.frame.timestamp_ms,true)}`} onClose={()=>setView(null)} wide><div className="ask-retained"><img src={frameUrl(runId,view.turn.id,view.evidence.id)} alt="Retained pixels cited by this answer"/>{(view.evidence.people as unknown as PersonFrame[]|undefined)?.flatMap(p=>p.people??[]).map((person,i)=><span key={person.id} className="ask-person-box" style={{left:`${person.box[0]*100}%`,top:`${person.box[1]*100}%`,width:`${person.box[2]*100}%`,height:`${person.box[3]*100}%`}}><span>Person {i+1} · {Math.round(person.confidence*100)}%</span></span>)}</div><p className="ask-caption">Retained frame at source timestamp {view.evidence.frame.timestamp_ms} ms. Detection confidence is not safety confidence. The player is paused at this moment.</p>{view.evidence.parent_frame_id&&<><h3>Original frame</h3><img className="ask-parent" src={frameUrl(runId,view.turn.id,view.evidence.parent_frame_id)} alt="Parent frame for the detail crop"/><p className="ask-caption">Detail crop: {view.evidence.box?.map(n=>Math.round(n*100)+'%').join(', ')} (x, y, width, height).</p></>}<details><summary>Evidence record</summary><pre>{JSON.stringify(view.evidence,null,2)}</pre></details></Dialog>}
 </section>;
}
