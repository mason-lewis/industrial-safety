import {useState} from 'react';
import type {HumanDecision, SourceExcerpt, VideoSafetyCaseView,PersonFrame} from '../api/generated';
import {categoryName, issueDescription, issueInterval, formatTime, issueState, readable} from './reviewPresentation';
import ReviewDialog from './ReviewDialog';
import Icon from './ReviewIcon';
import PersonEvidence from './PersonEvidence';

export default function ReviewIssueDetails({item,citations,decisions,people=[],relatedCases=[],onSelectCase,busy,onClose,onDecide,onAsk}: {
  item:VideoSafetyCaseView;citations:SourceExcerpt[];decisions:HumanDecision[];busy:boolean;onClose:()=>void;
  onDecide:(target:string,decision:'accepted'|'rejected'|'uncertain',reason:string)=>Promise<boolean>;
  relatedCases?:VideoSafetyCaseView[];onSelectCase?:(item:VideoSafetyCaseView)=>void;
  people?:PersonFrame[];onAsk?:()=>void;
}) {
  const [note,setNote]=useState(''),[decision,setDecision]=useState<'accepted'|'rejected'|'uncertain'>('uncertain');
  const [saved,setSaved]=useState(false);
  const assessment=item.assessment;
  const refs=[...new Set(item.screenings.flatMap(s=>s.check.evidence_refs))];
  const sourceIds=[...new Set(assessment?.findings.flatMap(f=>f.source_refs??[])??[])];
  const challenge=assessment?.challenge;
  const issues=Array.isArray(challenge?.issues)?challenge.issues as {problem?:string;alternative?:string}[]:[];
  const challengeLimits=Array.isArray(challenge?.limitations)?challenge.limitations as string[]:[];
  const ownDecisions=decisions.filter(d=>d.target_id===item.case.id);
  return <ReviewDialog title={categoryName(item.case.category)} onClose={onClose} wide>
    {relatedCases.length>1&&<label className="occurrence-picker">Observation<select value={item.case.id} onChange={e=>{const next=relatedCases.find(i=>i.case.id===e.target.value);if(next)onSelectCase?.(next);}}>{relatedCases.map(i=><option key={i.case.id} value={i.case.id}>{formatTime(issueInterval(i).start,true)}–{formatTime(issueInterval(i).end,true)} · {issueState(i)}{decisions.some(d=>d.target_id===i.case.id)?' · decision saved':''}</option>)}</select><small>Evidence and decisions apply to the selected observation.</small></label>}
    <div className="issue-meta"><span className="issue-time">{formatTime(issueInterval(item).start,true)} – {formatTime(issueInterval(item).end,true)}</span><span className={`status-pill ${assessment?'tone-blue':'tone-amber'}`}>{issueState(item)}</span></div>
    <p className="issue-lead">{issueDescription(item)}</p>
    {onAsk&&<button className="text-button issue-ask" onClick={onAsk}><Icon name="eye"/>Ask about this finding</button>}
    <div className="evidence-grid">{refs.map((id,i)=>{const frame=people.find(p=>p.frame_id===id);return <a key={id} href={`/api/media/frames/${id}`} target="_blank" rel="noreferrer">{frame?<PersonEvidence frame={frame} alt={`Retained evidence ${i+1} with person detections`}/>:<img src={`/api/media/frames/${id}`} alt={`Retained evidence ${i+1} for ${categoryName(item.case.category)}`}/>}<span>Evidence {i+1}<Icon name="expand"/></span></a>;})}</div>
    {people.some(p=>refs.includes(p.frame_id))&&<p className="muted-copy evidence-note">Person boxes: standard detector, numbered per image. The team checks the original pixels.</p>}
    <section className="detail-section"><div className="section-label"><Icon name="shield"/><h3>Review team</h3></div>
      {assessment?<>{assessment.findings.map(f=><article className="finding-copy" key={f.id}><span className="subtle-label">{readable(f.classification??'insufficient_evidence')}</span><p>{f.conclusion}</p>{!!f.recommendations?.length&&<><h4>Recommended next steps</h4><ul>{f.recommendations.map((r,i)=><li key={i}>{r}</li>)}</ul></>}{f.corrective_actions?.map((a,i)=><div className="action-note" key={i}><strong>{a.action}</strong><p>Verify: {a.verification}</p></div>)}{f.uncertainty?.map((u,i)=><p className="uncertainty-note" key={i}>{u}</p>)}</article>)}{!assessment.findings.length&&<p>The team returned no supported findings. Check the limitations before making a decision.</p>}{!!item.unassessed_screening_ids?.length&&<p className="notice-inline">{item.unassessed_screening_ids.length} additional occurrence(s) still need review.</p>}{assessment.limitations.map((l,i)=><p className="muted-copy" key={i}>{l}</p>)}</>:<div className="team-pending"><span className="status-orbit"/><div><strong>{issueState(item)}</strong><p>The concern is saved. It remains provisional until the team finishes.</p>{item.review_job?.error&&<p className="notice-inline">{item.review_job.error.message}</p>}</div></div>}
    </section>
    <details className="detail-disclosure"><summary>Evidence, sources & independent check <Icon name="chevron"/></summary><div>
      <h4>Original screening observations</h4>{item.screenings.map(s=><article key={s.id}><strong>{formatTime(s.check.start_ms)} · {readable(s.check.visibility)} visibility</strong><p>{s.check.condition}</p>{s.check.limitations?.map((l,i)=><p className="muted-copy" key={i}>{l}</p>)}</article>)}
      <h4>Independent check</h4>{!challenge?<p>Not completed yet.</p>:<>{issues.length?issues.map((issue,i)=><article key={i}><p>{issue.problem}</p><p className="muted-copy">Alternative: {issue.alternative}</p></article>):<p>{Array.isArray(challenge.checked_finding_ids)&&challenge.checked_finding_ids.length?'The independent reviewer checked the findings and recorded no objections.':'No completed finding checks were recorded.'}</p>}{challengeLimits.map((l,i)=><p className="muted-copy" key={i}>{l}</p>)}</>}
      <h4>Source guidance</h4><p className="muted-copy">A reference is not a legal determination. Site and jurisdiction applicability must be established.</p>{!sourceIds.length&&<p>No source guidance has been attached yet.</p>}{sourceIds.map(id=>{const source=citations.find(s=>s.id===id);return <article className="source-card" key={id}><strong>{source?.title??'Source unavailable'}</strong><small>{source?.section} · {source?.version} · {source?.jurisdiction}</small><p>{source?.text??'The cited text is unavailable and cannot support a verified conclusion.'}</p>{source?.url&&/^https?:\/\//.test(source.url)&&<a href={source.url} target="_blank" rel="noreferrer">Read original source <Icon name="arrow"/></a>}</article>;})}
    </div></details>
    <section className="detail-section decision-section" aria-label="Your decision"><div className="section-label"><Icon name="check"/><h3>{ownDecisions.length?'Your decision & history':'Your decision'}</h3></div>
      <form onSubmit={async e=>{e.preventDefault();if(note.trim()&&await onDecide(item.case.id!,decision,note.trim())){setNote('');setSaved(true);}}}>
        <div className="decision-choices" role="group" aria-label="Decision">{([['accepted','Accept','check'],['uncertain','Needs more evidence','eye'],['rejected','Dismiss','close']] as const).map(([value,label,icon])=><button type="button" key={value} aria-pressed={decision===value} onClick={()=>{setDecision(value);setSaved(false);}}><Icon name={icon}/>{label}</button>)}</div>
        <label>Your reason<textarea required value={note} onChange={e=>{setNote(e.target.value);setSaved(false);}} placeholder="What can you confirm, or what is missing?"/></label>
        <button className="solid-button" disabled={busy||!note.trim()} type="submit">Save decision <Icon name="check"/></button>{saved&&<p role="status" className="muted-copy">Decision saved to the report.</p>}</form>
      {ownDecisions.map(d=><article className="decision-record" key={d.id}><strong>{d.decision==='accepted'?'Accepted':d.decision==='rejected'?'Dismissed':'Needs more evidence'}</strong><p>{d.reason}</p><small>{d.reviewer_id} · {new Date(d.created_at??'').toLocaleString()}</small></article>)}
    </section>
  </ReviewDialog>;
}
