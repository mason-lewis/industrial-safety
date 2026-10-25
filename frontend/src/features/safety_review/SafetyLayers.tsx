import type {VideoSafetyOverview} from '../../api/generated';

const categories:Record<string,string>={ppe:'PPE',blocked_access:'Blocked access',unstable_stack:'Unstable stack',slip_trip:'Slip / trip',struck_by:'Struck-by',fall:'Fall',fire_burn:'Fire / burn'};
export default function SafetyLayers({value,onStop}:{value:VideoSafetyOverview;onStop:(id:string)=>void}){
 const suspected=value.screenings.filter(signal=>signal.check.result==='suspected').length;
 const unknown=value.screenings.filter(signal=>signal.check.result==='unknown').length;
 const fixture=value.screenings.some(signal=>signal.mode==='fixture');
 return <section className="sandbox-safety" aria-label="Two safety layers">
  <h3>Layer 1 · Visual safety screening</h3>
  <p>{value.screenings.length} checks · {suspected} suspected concerns · {unknown} unknown</p>
  {fixture&&<p className="sandbox-footnote">Demo signals and specialist answers are fixed examples, not real hazard findings.</p>}
  <details><summary>View screening checks</summary>{value.screenings.map(signal=><article key={signal.id}>
   <strong>{categories[signal.check.category]} · {signal.check.result}</strong><p>{signal.check.condition}</p>
   {signal.check.limitations?.map((text,i)=><p className="muted" key={i}>{text}</p>)}
  </article>)}</details>
  <h3>Layer 2 · Safety / OSHA team</h3>
  {!value.cases.length&&<p className="muted">No condition has requested specialist judgment in the sampled frames.</p>}
  {[...value.cases].sort((a,b)=>Number(b.priority==='high')-Number(a.priority==='high')).map((item,index)=><details key={item.case.id} className="sandbox-safety-case" open={index===0}>
   <summary><strong>{categories[item.case.category]}</strong>{Number.isFinite(item.case.start_ms)&&<span> · {(item.case.start_ms/1000).toFixed(1)}–{(item.case.end_ms/1000).toFixed(1)} s</span>} · <span className="sandbox-verdict">{item.status.replaceAll('_',' ')}</span></summary>
   {item.priority==='high'&&<p className="sandbox-attention">{fixture?'Demo high priority':'Immediate-action concern'} · {item.assessment?.triage_supported?'Specialist-supported urgency':'Provisional concern'}. Report delivery is not configured. Verify the event is current before any manager notification.</p>}
   <p className="sandbox-footnote">Event calendar time: {item.case.event_time_utc??'unknown; source timestamps only'}.</p>
   <p className="sandbox-footnote">Report input: {item.report_condition==='immediate'?'immediate action':'scheduled summary'} · {item.review_job?'Specialist handoff recorded.':item.status==='deferred'?'Review deferred or disabled; the concern is retained.':'Waiting for video review to complete.'}</p>
   {item.review_job?.error&&<p className="sandbox-error">{item.review_job.error.message}</p>}
   {item.review_job?.id&&['queued','running'].includes(item.review_job.status??'')&&<button onClick={()=>onStop(item.review_job!.id!)}>Stop specialist review</button>}
   {item.assessment?.findings.map(finding=><div key={finding.id}>
    <p><strong>{finding.conclusion}</strong></p><p className="muted">{finding.classification?.replaceAll('_',' ')} · {finding.status}</p>
    {!!finding.uncertainty?.length&&<p className="muted">{finding.uncertainty?.join(' ')}</p>}
    {finding.corrective_actions?.map((action,i)=><p key={i}>{action.action}<br/><small>Verify: {action.verification}</small></p>)}
    {!!finding.source_refs?.length&&<details><summary>Stored source references</summary><p>{finding.source_refs?.join(', ')}</p><p className="muted">Exact clauses and specialist/source context are retained in the review record.</p></details>}
   </div>)}
   {!!item.assessment?.limitations.length&&<details><summary>Judgment limits</summary><ul>{item.assessment.limitations.map((text,i)=><li key={i}>{text}</li>)}</ul></details>}
  </details>)}
 </section>;
}
