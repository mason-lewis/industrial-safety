import {useEffect,useRef,useState} from 'react';
import {request} from '../../api/client';
import type {AuditEvent,Job,RecordedSource,VideoAnalysis as Analysis,VideoChunk,VideoSafetyOverview} from '../../api/generated';
import {AgentInsights,UsageMonitor} from '../../components/AgentInsights';
import {chatUsage} from '../../components/agentTrace';
import ChatMarkdown from './ChatMarkdown';
import {readChecks,saveCheck,type SandboxCheck,type Verdict} from './sandboxChecks';
import './videoAnalysis.css';
import SafetyLayers from './SafetyLayers';

const defaultQuestion='Summarize the visible events and cite the key moments.';
const time=(ms:number)=>`${(ms/1000).toFixed(2)} s`;
const verdictLabels:Record<Verdict,string>={matches:'Looks right',does_not_match:'Something is wrong',unsure:'Cannot tell'};
const modeLabels:Record<string,string>={recorded_video_observer:'Video observer',recorded_video_planner:'Video planner',recorded_video_verifier:'Independent video verifier'};

export default function VideoAnalysis({jobs,refresh}:{jobs:Job[];refresh:()=>Promise<void>}){
 const [sources,setSources]=useState<RecordedSource[]>([]),[source,setSource]=useState('');
 const [question,setQuestion]=useState(defaultQuestion),[mode,setMode]=useState<'hosted'|'fixture'>('hosted');
 const [safetyEnabled,setSafetyEnabled]=useState(true),[ppe,setPpe]=useState('');
 const [safety,setSafety]=useState<VideoSafetyOverview|null>(null);
 const [selected,setSelected]=useState(''),[loadedJob,setLoadedJob]=useState<Job|null>(null);
 const [chunks,setChunks]=useState<VideoChunk[]>([]),[result,setResult]=useState<Analysis|null>(null),[audit,setAudit]=useState<AuditEvent[]>([]);
 const [error,setError]=useState(''),[submitting,setSubmitting]=useState(false),[uploading,setUploading]=useState(false);
 const [loadError,setLoadError]=useState('');
 const [uploaded,setUploaded]=useState<{id:string;name:string;url:string}|null>(null);
 const [previewFailed,setPreviewFailed]=useState(false);
 const [checks,setChecks]=useState<SandboxCheck[]>([]),[notes,setNotes]=useState('');
 const uploadInput=useRef<HTMLInputElement>(null);
 const videoJobs=jobs.filter(job=>job.kind==='video_analysis');
 const active=loadedJob?.id===selected?loadedJob:videoJobs.find(job=>job.id===selected);
 const running=Boolean(active&&['queued','running'].includes(active.status??''));
 const busy=submitting||uploading||running;
 const latestCheck=checks.at(-1);
 const frames=new Map(chunks.flatMap(chunk=>chunk.frames.map(frame=>[frame.id,frame] as const)));
 const citedRefs=[...new Set(result?.answer.claims?.flatMap(claim=>claim.evidence_refs)??[])];
 const labels=Object.fromEntries(audit.filter(row=>row.run_id&&typeof row.configuration?.agent_mode==='string').map(row=>[row.run_id!,modeLabels[String(row.configuration!.agent_mode)]??row.agent_name??'Video agent']));
 const lastMode=audit.find(row=>row.event_type==='model_request')?.configuration?.agent_mode;
 const progress=active?.status==='queued'?'Waiting to start':lastMode==='recorded_video_verifier'?'Checking the answer':lastMode==='recorded_video_planner'?'Choosing the next inspection':'Inspecting the video';
 const inspectionCount=chunks.reduce((total,chunk)=>total+chunk.intervals.length,0);

 async function loadSources(){const rows=await request<RecordedSource[]>('/video-analysis/sources');setSources(rows);return rows;}
 useEffect(()=>{
  let current=true;
  request<RecordedSource[]>('/video-analysis/sources').then(rows=>{if(current)setSources(rows);}).catch(e=>{if(current)setError(String(e));});
  return()=>{current=false;};
 },[]);
 useEffect(()=>()=>{if(uploaded)URL.revokeObjectURL(uploaded.url);},[uploaded]);
 useEffect(()=>{
  let current=true;let timer:ReturnType<typeof setTimeout>|undefined;
  setChunks([]);setAudit([]);setResult(null);setLoadedJob(null);setChecks([]);setNotes('');setSafety(null);setLoadError('');
  if(!selected)return;
  try{const saved=readChecks(localStorage,selected);setChecks(saved);setNotes(saved.at(-1)?.notes??'');}catch(e){setError(String(e));}
  async function load(){
   try{
    const job=await request<Job>(`/jobs/${selected}`);
    const [memory,trace,answer,layers]=await Promise.all([
     request<VideoChunk[]>(`/video-analysis/jobs/${selected}/chunks`),
     request<AuditEvent[]>(`/video-analysis/jobs/${selected}/audit?limit=1000&include_safety=true`),
     job.status==='completed'&&job.result_refs?.[0]?request<Analysis>(`/video-analysis/${job.result_refs[0]}`):Promise.resolve(null),
     request<VideoSafetyOverview>(`/video-analysis/jobs/${selected}/safety`),
    ]);
    if(current){setLoadedJob(job);setChunks(memory);setAudit(trace);setResult(answer);setSafety(layers);setLoadError('');}
   }catch(e){if(current)setLoadError(String(e));}
   if(current)timer=setTimeout(load,2000);
  }
  void load();return()=>{current=false;if(timer)clearTimeout(timer);};
 },[selected]);

 async function upload(file:File){
  setUploading(true);setError('');
  try{
   const before=await loadSources();
   const response=await fetch(`/api/monitoring/uploads?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
   if(!response.ok){const body=await response.json().catch(()=>null);throw new Error(body?.detail??'Video upload failed.');}
   const after=await loadSources();
   const added=after.filter(item=>!before.some(old=>old.id===item.id));
   if(added.length===1){setSource(added[0].id);setPreviewFailed(false);setUploaded({id:added[0].id,name:file.name,url:URL.createObjectURL(file)});}
   else{setSource('');setError('Video uploaded. Choose it from the library below.');}
  }catch(e){setError(String(e));}finally{setUploading(false);if(uploadInput.current)uploadInput.current.value='';}
 }
 async function submit(){
  if(busy)return;
  setSubmitting(true);setError('');
  try{
   const job=await request<Job>('/video-analysis',{source_id:source,question,mode,safety_screening:safetyEnabled,escalate_to_safety_team:safetyEnabled,required_ppe:ppe.split(',').map(item=>item.trim()).filter(Boolean),idempotency_key:crypto.randomUUID()});
   if(!job.id)throw new Error('The server did not return a test ID.');
   setSelected(job.id);await refresh();
  }catch(e){setError(String(e));}finally{setSubmitting(false);}
 }
 async function cancel(){
  try{setLoadedJob(await request<Job>(`/jobs/${selected}/cancel`,{}));await refresh();}catch(e){setError(String(e));}
 }
 function chooseTest(id:string){
  setError('');setSelected(id);
  const job=videoJobs.find(item=>item.id===id);
  if(job){setQuestion(String(job.payload.question??defaultQuestion));setSource(String(job.payload.source_id??''));setMode(job.payload.mode==='fixture'?'fixture':'hosted');setSafetyEnabled(job.payload.safety_screening!==false);setPpe(Array.isArray(job.payload.required_ppe)?job.payload.required_ppe.join(', '):'');}
 }
 function check(verdict:Verdict){
  if(!result?.id)return;
  try{
   setChecks(saveCheck(localStorage,{jobId:selected,analysisId:result.id,verdict,notes,createdAt:new Date().toISOString()}));setError('');
  }catch(e){setError(`Could not save your check: ${String(e)}`);}
 }
 function exportTest(){
  if(!result)return;
  const report={format:'video-sandbox-test-v1',exported_at:new Date().toISOString(),analysis:result,chunks,safety_layers:safety,safety_layers_state:safety?.cases.some(item=>['queued','running','awaiting_video_completion'].includes(item.status))?'pending':'snapshot',export_notice:'Safety results reflect their current statuses; this export is not a delivered report.',personal_checks:checks,personal_check_storage:'Browser-local; does not change backend findings or labels.'};
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='video-sandbox-test.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 return <div className="video-analysis">
  {(error||loadError)&&<p className="sandbox-error" role="alert">{error||loadError}</p>}
  <div className="sandbox-layout">
   <section className="panel sandbox-input" aria-label="Set up a video test">
    <h2>1. Choose a video</h2>
    <input ref={uploadInput} aria-label="Upload a video file" type="file" accept="video/*,.mp4,.mov,.mkv,.avi,.webm,.m4v" hidden onChange={e=>{const file=e.target.files?.[0];if(file)void upload(file);}}/>
    <div className="sandbox-upload"><button disabled={busy} onClick={()=>uploadInput.current?.click()}>{uploading?'Uploading…':'Upload video'}</button><button className="sandbox-quiet" disabled={uploading} onClick={()=>loadSources().catch(e=>setError(String(e)))}>Refresh</button></div>
    <label>Or choose from your library<select aria-label="Video library" value={source} disabled={busy} onChange={e=>setSource(e.target.value)}>
     <option value="">Choose a video</option>{sources.map(item=><option key={item.id} value={item.id}>{uploaded?.id===item.id?uploaded.name:item.name}</option>)}
    </select></label>
    {!sources.length&&!uploading&&<p className="muted">Upload an MP4 to get started.</p>}
    {uploaded?.id===source&&(previewFailed?<p className="sandbox-footnote">Preview unavailable in this browser. You can still analyze the video.</p>:<video className="sandbox-preview" src={uploaded.url} controls preload="metadata" aria-label="Preview uploaded video" onError={()=>setPreviewFailed(true)}/>)}
    <h2>2. Ask a question</h2>
    <label className="sandbox-question-label">What should the AI check?<textarea aria-label="Video question" value={question} disabled={running} onChange={e=>setQuestion(e.target.value)} maxLength={4000}/></label>
    <div className="sandbox-presets"><button disabled={busy} onClick={()=>setQuestion(defaultQuestion)}>Summarize</button><button disabled={busy} onClick={()=>setQuestion('What changes between the beginning and end of the video? Cite the frames that support each change.')}>Find changes</button></div>
    <label className="sandbox-safety-toggle"><input type="checkbox" checked={safetyEnabled} disabled={busy} onChange={e=>setSafetyEnabled(e.target.checked)}/>Screen safety and involve specialists when needed</label>
    <details><summary>Optional PPE checks</summary><label>PPE to look for<input value={ppe} disabled={busy} onChange={e=>setPpe(e.target.value)} placeholder="hard hat, eye protection" maxLength={480}/></label><p className="sandbox-footnote">Up to eight comma-separated items. The safety team still checks applicability.</p></details>
    <fieldset className="sandbox-modes"><legend>Run with</legend><label><input type="radio" name="video-test-mode" value="hosted" checked={mode==='hosted'} disabled={busy} onChange={()=>setMode('hosted')}/>AI analysis</label><label><input type="radio" name="video-test-mode" value="fixture" checked={mode==='fixture'} disabled={busy} onChange={()=>setMode('fixture')}/>Free demo</label></fieldset>
    <p className="sandbox-mode-note">{mode==='fixture'?'Fixed demo answers. Tests the workflow, not AI accuracy.':'Uses your configured AI budget. Nothing runs until you click below.'}</p>
    <button className="sandbox-run" disabled={!source||!question.trim()||busy} onClick={submit}>{submitting?'Starting…':running?'Test in progress':mode==='fixture'?'Run free demo':'Run analysis'}</button>
    <p className="sandbox-footnote">Sampled frames only. Audio and continuous coverage are not analyzed.</p>
   </section>
   <section className="panel sandbox-output" aria-label="Test result and validation">
    <div className="panel-title"><h2>3. Check the result</h2>{result&&<span className={`sandbox-verdict ${result.status==='supported_within_samples'?'is-supported':''}`}>{result.mode==='fixture'?'Demo result':result.status==='supported_within_samples'?'Supported within samples':'Needs review'}</span>}</div>
    {!selected&&<div className="sandbox-empty"><span>Ready when you are</span><p>Your answer, cited frames, and independent check will appear here.</p></div>}
    {running&&<div className="sandbox-running" role="status"><span className="sandbox-pulse"/><div><strong>{progress}</strong><p>{inspectionCount?`${inspectionCount} segment inspections recorded.`:'Your test is queued for processing.'}</p></div><button onClick={cancel}>Stop</button></div>}
    {active?.error&&<div className="sandbox-error" role="alert"><strong>{active.status==='cancelled'?'Test stopped':'Test did not finish'}</strong><p>{active.error.message}</p><p>Earlier evidence and records are kept. Run again to create a new test.</p></div>}
    {safety&&safety.screenings.length>0&&<SafetyLayers value={safety} onStop={id=>{request(`/jobs/${id}/cancel`,{}).then(()=>refresh()).catch(e=>setError(String(e)));}}/>}
    {result&&<>
     <p className="sandbox-result-question">{result.question}</p>
     {result.mode==='fixture'&&<p className="sandbox-demo-notice">Free demo: the responses below are fixed examples, not an analysis of this video.</p>}
     <ChatMarkdown text={result.answer.answer}/>
     <div className="sandbox-checks">{result.answer.claims?.map(claim=>{const review=result.verification.checks?.find(item=>item.claim_id===claim.id);return <article key={claim.id}>
      <div><strong>{claim.text}</strong><span className="sandbox-time">{time(claim.start_ms)}–{time(claim.end_ms)}</span></div>
      <p><span className={`sandbox-verdict ${review?.verdict==='supported'?'is-supported':''}`}>{review?.verdict==='supported'?'Supported':review?.verdict==='unsupported'?'Not supported':'Uncertain'}</span> {review?.explanation}</p>
      {!!claim.uncertainty?.length&&<p className="muted">{claim.uncertainty?.join(' ')}</p>}
      <div className="sandbox-frame-links">{claim.evidence_refs.map(ref=><a key={ref} href={`#sandbox-frame-${citedRefs.indexOf(ref)}`}>Frame {citedRefs.indexOf(ref)+1}</a>)}</div>
     </article>;})}</div>
     {citedRefs.length>0&&<div className="sandbox-evidence" aria-label="Cited evidence frames">{citedRefs.map((ref,index)=>{const frame=frames.get(ref);return frame?<figure id={`sandbox-frame-${index}`} key={ref}>
      <a href={frame.image_url} target="_blank" rel="noreferrer"><img src={frame.image_url} alt={`Frame ${index+1} at ${time(frame.timestamp_ms)}`}/></a><figcaption>Frame {index+1} · {time(frame.timestamp_ms)}</figcaption>
     </figure>:<p key={ref}>Frame {index+1} is unavailable.</p>;})}</div>}
     <details className="sandbox-limits"><summary>Coverage and limitations ({result.limitations.length})</summary><ul>{result.limitations.map((text,index)=><li key={index}>{text}</li>)}</ul></details>
     <section className="sandbox-personal-check" aria-label="Your validation check"><h3>Your check</h3><p>Compare the answer with the video and cited frames.</p>
      <div className="sandbox-validation-buttons">{(Object.keys(verdictLabels) as Verdict[]).map(verdict=><button key={verdict} aria-pressed={latestCheck?.verdict===verdict} onClick={()=>check(verdict)}>{verdictLabels[verdict]}</button>)}</div>
      <details><summary>Optional notes</summary><label>Validation notes<textarea aria-label="Validation notes" value={notes} onChange={e=>setNotes(e.target.value)} placeholder="What did you confirm or find missing?" maxLength={4000}/></label><p className="muted">Select a check above to save these notes.</p></details>
      <p className="sandbox-footnote">{latestCheck?`Saved in this browser: ${verdictLabels[latestCheck.verdict]}.`:'Your check is saved in this browser.'} This does not approve labels or change the AI result.</p>
     </section>
     <button className="sandbox-quiet" onClick={exportTest}>Download test report</button>
    </>}
    {selected&&!running&&!result&&!active?.error&&<p className="muted" role="status">Loading saved test…</p>}
   </section>
  </div>
  <details className="panel sandbox-history"><summary>Previous tests ({videoJobs.length})</summary><label>Open a test<select aria-label="Previous test" value={selected} onChange={e=>chooseTest(e.target.value)}><option value="">New test</option>{videoJobs.map(job=><option key={job.id} value={job.id}>{String(job.payload.question).slice(0,80)} · {job.payload.mode==='fixture'?'Demo':'AI'} · {job.status}</option>)}</select></label></details>
  <details className="panel sandbox-diagnostics"><summary>Run details &amp; troubleshooting</summary>
   {selected&&<>
    <AgentInsights rows={audit} status={active?.status??'Loading'} selected title="Recorded activity" labels={labels}/>
    <UsageMonitor usage={chatUsage(audit)} selected fixture={active?.payload.mode==='fixture'} partial={audit.length>=1000} title="Tokens & cost · this test"/>
    {active&&!running&&!safety?.cases.some(item=>['queued','running'].includes(item.status))&&<a href={`/api/video-analysis/jobs/${selected}/audit/download?include_safety=true`}>Download full pipeline audit</a>}
    <details><summary>Inspection plans and tool results</summary>{audit.filter(row=>['video_inspection_planned','tool_finished','validation_failed','run_failed'].includes(row.event_type)).reverse().slice(-200).map(row=><details key={row.id}><summary>{row.event_type.replaceAll('_',' ')} · {row.tool_name??row.agent_name??row.status}</summary><pre>{JSON.stringify({arguments:row.arguments,output:row.output,error:row.error},null,2)}</pre></details>)}</details>
    {chunks.length>0&&<details><summary>Inspected segments ({inspectionCount})</summary>{chunks.map(chunk=><article key={chunk.id}><p><strong>{chunk.intervals.map(interval=>`${time(interval.start_ms)}–${time(interval.end_ms)}`).join(' / ')}</strong></p><p>{chunk.summary}</p>{chunk.observations.map((observation,index)=><p key={index}>{observation.description} · {time(observation.start_ms)}–{time(observation.end_ms)}</p>)}<ul>{chunk.limitations.map((text,index)=><li key={index}>{text}</li>)}</ul></article>)}</details>}
   </>}
   <details><summary>If a test stays queued</summary><p>Start the review worker in a terminal:</p><pre>./scripts/safety worker review</pre><p>The worker processes one job at a time. A queued test can also be waiting behind earlier work.</p></details>
  </details>
 </div>;
}
