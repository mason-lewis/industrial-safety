import {useEffect,useRef,useState} from 'react';
import type {AuditEvent,ReviewActivityPage,ReviewRunStatus} from '../../api/generated';
import {request} from '../../api/client';
import AgentOrchestration from '../../components/AgentOrchestration';
import {buildOrchestration} from '../../components/orchestration';
import {chatUsage} from '../../components/agentTrace';
import {formatTime} from '../../components/reviewPresentation';
import Icon from '../../components/ReviewIcon';
import './reviewActivity.css';
import {activityEnd,activityRows,activityDuration} from './reviewActivityEvents';

// Live playback can end before its queued screening windows finish, so window and team jobs count too.
export const activityActive=(status:ReviewRunStatus)=>['queued','running'].includes(status.run.state??'')
 ||(status.windows??[]).some(w=>['queued','running'].includes(w.status??''))
 ||status.safety.cases.some(c=>['queued','running'].includes(c.review_job?.status??''));

export default function ReviewActivity({status,onSeek}:{status:ReviewRunStatus;onSeek:(ms:number)=>void}){
 const [rows,setRows]=useState<AuditEvent[]>([]),[error,setError]=useState('');
 const [now,setNow]=useState(Date.now());
 const live=activityActive(status),progress=`${status.run.state}:${status.completed_windows}:${status.safety.cases.length}:${status.pending_specialists}`,cursor=useRef({run:'',sequence:0});
 useEffect(()=>{
  let disposed=false,timer:ReturnType<typeof setTimeout>;
  if(cursor.current.run!==status.run.id){cursor.current={run:status.run.id??'',sequence:0};setRows([]);setError('');}
  async function load(){try{
   // Pages hold at most 200 events: drain the backlog immediately, then poll while work is active.
   let page:ReviewActivityPage;
   do{page=await request<ReviewActivityPage>(`/review-runs/${status.run.id}/activity?after_sequence=${cursor.current.sequence}`);
    if(disposed)return;cursor.current.sequence=page.cursor;const events=page.events;setRows(old=>[...old,...events].slice(-4000));}while(page.events.length>=200&&!disposed);
   setNow(Date.now());setError('');
  }catch{if(!disposed)setError('Activity connection interrupted. Reconnecting…');}
  if(!disposed&&live)timer=setTimeout(load,1000);}
  void load();return()=>{disposed=true;clearTimeout(timer);};
 },[status.run.id,live,progress]);
 useEffect(()=>{if(!live)return;const id=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(id);},[live]);
 const orchestration=buildOrchestration(rows,status.run.kind==='live'?'Live coordinator':'Review coordinator',now);
 const usage=chatUsage(rows);
 const recent=activityRows(rows);
 return <section className="review-activity agent-team-card" aria-label="Agent team">
  <AgentOrchestration orchestration={orchestration} now={now} title="Agent team" empty={live?'Waiting for the first agent…':'No agent activity was recorded for this run.'}/>
  {(usage.unknownCost>0||usage.inFlight>0)&&<p className="notice-inline">{usage.unknownCost>0?`${usage.unknownCost} request(s) have unresolved cost; totals may be incomplete.`:`${usage.inFlight} request(s) awaiting a response.`}</p>}
  {error&&<p className="muted-copy" role="status">{error}</p>}
  <details className="detail-disclosure"><summary>Technical log <Icon name="chevron"/></summary><div>
   <ol aria-label="Agent and tool activity">{recent.map(r=>{
    const end=activityEnd(r,rows);
    const elapsed=activityDuration(r,end,now);
    return <li key={r.sequence}><strong>{r.agent_name?.replaceAll('_',' ')??'Local coordinator'}</strong> · {r.tool_name??r.event_type.replaceAll('_',' ')} · {end?.status??r.status}{elapsed!=null&&` · ${(elapsed/1000).toFixed(1)}s`}</li>;
   })}</ol>
   {!!status.detail_inspections?.length&&<details><summary>Closer frame inspections</summary>{status.detail_inspections.map(d=><article key={d.id}><p>{d.category.replaceAll('_',' ')} · {d.status}{d.reason&&` · ${d.reason}`}</p><div className="evidence-grid">{d.crops.map((c,i)=><div key={i}><button className="text-button" onClick={()=>onSeek(Number(c.timestamp_ms))}><img src={`/api/review-runs/${status.run.id}/details/${encodeURIComponent(d.id??'')}/crops/${i}`} alt={`Original-pixel crop at ${formatTime(Number(c.timestamp_ms),true)}`}/>{formatTime(Number(c.timestamp_ms),true)} · seek video</button><a href={`/api/media/frames/${String(c.parent_frame_id)}`} target="_blank" rel="noreferrer">Full context frame</a></div>)}</div></article>)}</details>}
   <details><summary>Raw events and accounting</summary>{status.test_budget&&<p>Cumulative test commitment: ${Number(status.test_budget.committed_usd).toFixed(6)}.</p>}<pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{JSON.stringify({transport:status.transport_diagnostics,budget:status.test_budget,events:rows.slice(-400)},null,2)}</pre></details>
  </div></details>
 </section>;
}
