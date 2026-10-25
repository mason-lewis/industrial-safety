import {useEffect,useRef,useState} from 'react';
import {request} from '../api/client';
import {needsAIDraft,runPreparation} from './preparationLogic';
import type {LearningSampleInput} from './generated';
import type {ReviewFrame} from './FrameReview';
export interface PreparationClip{id:string;status:string;frames:number;split:string;sampling_history?:{added:number;eligible:number;remaining:number;mode:string}[]}
interface Props{projectId:string;clips:PreparationClip[];frames:ReviewFrame[];frozen:boolean;busy:boolean;dirty:boolean;aiReady:boolean;role:string;onBusy:(value:boolean)=>void;onAIWorking:(value:boolean)=>void;onRefresh:()=>Promise<unknown>;onFrame:(id:string)=>void}
export default function FramePreparation({projectId,clips,frames,frozen,busy,dirty,aiReady,role,onBusy,onAIWorking,onRefresh,onFrame}:Props){
 const [mode,setMode]=useState<LearningSampleInput['mode']>('coverage'),[interval,setIntervalS]=useState(2),[maximum,setMaximum]=useState(20),[limit,setLimit]=useState(25),[scope,setScope]=useState('all'),[running,setRunning]=useState(false),[message,setMessage]=useState(''),[done,setDone]=useState(0),[total,setTotal]=useState(0);
 const stop=useRef(false);useEffect(()=>()=>{stop.current=true;},[projectId]);
 const scoped=clips.filter(c=>(scope==='all'||c.id===scope)&&(role==='all'||role===c.split));
 const pending=frames.filter(f=>scoped.some(c=>c.id===f.clip_id)&&needsAIDraft(f));
 const ready=frames.filter(f=>scoped.some(c=>c.id===f.clip_id)&&!f.reviewed&&f.ai_review?.revision===(f.revision??0));
 const blocked=busy||frozen||dirty;
 const process=async(kind:'sample'|'ai')=>{
  const items=kind==='sample'?scoped.filter(c=>['uploaded','failed','ready'].includes(c.status)):pending.slice(0,limit);
  if(!items.length)return;
  stop.current=false;setRunning(true);onBusy(true);onAIWorking(kind==='ai');setDone(0);setTotal(items.length);setMessage(kind==='sample'?'Extracting frames locally…':'Preparing AI box drafts…');
  try{const {completed,error:failure}=await runPreparation<PreparationClip|ReviewFrame>(items,async item=>{
    if(kind==='sample')await request(`/learning/projects/${projectId}/clips/${item.id}/sample`,{mode,interval_s:interval,max_frames:maximum} satisfies LearningSampleInput);
    else{const f=item as ReviewFrame;onFrame(f.id);await request(`/learning/projects/${projectId}/frames/${f.id}/image-review`,{expected_revision:f.revision??0});}
    await onRefresh();
   },()=>stop.current,setDone);
   if(kind==='ai'&&completed>0)onFrame(items[0].id);
   setMessage(`${completed}/${items.length} ${kind==='sample'?'clips sampled':'AI drafts prepared'}.${failure?` Stopped: ${failure}`:stop.current?' Stopped after the current item.':' Completed.'} Completed work is saved. ${kind==='ai'?'Review drafts and accept, reject or adjust; no labels were approved.':''}`);
  }finally{onBusy(false);onAIWorking(false);setRunning(false);}
 };
 return <section className="panel" aria-label="Batch frame preparation"><h2>Prepare frames and AI boxes in batches</h2><p>Cover the whole recording, then let the agent draw and correct boxes before you review. Existing labels are preserved.</p><div className="learning-form-grid">
 <label>Clips to prepare<select aria-label="Clips to prepare" disabled={busy} value={scope} onChange={e=>setScope(e.target.value)}><option value="all">All clips in the selected dataset role</option>{clips.map((c,i)=><option key={c.id} value={c.id}>Clip {i+1} · {c.id.slice(0,8)} · {c.frames} frames</option>)}</select></label>
 <label>Sampling method<select aria-label="Sampling method" disabled={busy} value={mode} onChange={e=>setMode(e.target.value as LearningSampleInput['mode'])}><option value="coverage">Spread samples across the whole clip</option><option value="interval">Next frames at a time interval</option><option value="every_frame">Every decoded frame · next bounded batch</option></select></label>
 <label>Spacing (seconds)<input aria-label="Batch sample spacing" type="number" min=".5" max="60" step=".5" disabled={busy||mode==='every_frame'} value={interval} onChange={e=>setIntervalS(Number(e.target.value))}/></label>
 <label>New frames per clip<input aria-label="New frames per clip" type="number" min="1" max="200" disabled={busy} value={maximum} onChange={e=>setMaximum(Number(e.target.value))}/></label>
 </div><p className="muted">Whole-clip mode spreads the cap across eligible timestamps, rather than stopping at the beginning. Every-frame mode continues from unsampled frames on each run. Up to 200 new frames per clip per batch and 1,000 per project. Dense neighboring frames are correlated; source groups still determine splits.</p>
 <button disabled={blocked||!scoped.length||!Number.isInteger(maximum)||maximum<1||maximum>200||interval<.5||interval>60} onClick={()=>void process('sample')}>Sample {scoped.length} clips</button>
 <details><summary>Last sampling coverage</summary>{scoped.map(c=>{const h=c.sampling_history?.at(-1);return <p key={c.id}>Clip {c.id.slice(0,8)} · {c.frames} stored frames{h?` · last batch added ${h.added}/${h.eligible} eligible frames · ${h.remaining} left at that spacing`:''}</p>;})}</details>
 <div className="toolbar"><label>AI frames in this batch<input aria-label="AI frames in this batch" type="number" min="1" max="100" disabled={busy} value={limit} onChange={e=>setLimit(Number(e.target.value))}/></label><button className="primary" disabled={blocked||!aiReady||!pending.length||!Number.isInteger(limit)||limit<1||limit>100} onClick={()=>void process('ai')}>Prepare AI drafts for {Math.min(pending.length,limit)} frames</button><button disabled={busy||dirty||!ready.length} onClick={()=>onFrame(ready[0].id)}>Review {ready.length} ready AI drafts</button></div>
 <p>{pending.length} unreviewed frames need AI drafts · {ready.length} ready for human review. AI inspects only pixels, requested classes and candidate boxes. Folder names and recording metadata are excluded.</p><p className="muted">Starting AI preparation uses hosted requests and existing spending limits. It skips human-reviewed frames and current saved AI results, stops on the first error or budget block, and never trains or approves labels. Enable image review in Step 2 first.</p>
 {dirty&&<p>Save or discard the current frame edits before preparing a batch.</p>}{running&&<><progress aria-label="Frame preparation progress" value={done} max={total}/><p role="status">{done}/{total} completed · current item in progress</p><button onClick={()=>{stop.current=true;setMessage('Stopping after the current item finishes…');}}>Stop after current item</button></>}{message&&<p role="status">{message}</p>}
 </section>;
}
