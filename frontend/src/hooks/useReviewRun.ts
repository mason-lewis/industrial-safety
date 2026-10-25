import {useEffect, useRef, useState} from 'react';
import {issueInterval,liveSampleForDisplay} from '../components/reviewPresentation';
import {request} from '../api/client';
import {useAiControl} from './useAiControl';
import {useReviewPreferences} from './useReviewPreferences';
import type {FrameRef, Job, LivePersonSample, RecordedSource, ReviewRun, ReviewRunStatus, SourceExcerpt, VideoSafetyCaseView} from '../api/generated';

export function useReviewRun(kind:'live'|'review') {
  const ai=useAiControl();
  const [sources,setSources]=useState<RecordedSource[]>([]);
  const [runs,setRuns]=useState<ReviewRun[]>([]);
  const [selected,setSelected]=useState('');
  const [status,setStatus]=useState<ReviewRunStatus|null>(null);
  const [citations,setCitations]=useState<SourceExcerpt[]>([]);
  const mode='hosted' as const;
  const {ppe,setPpe,jurisdiction,setJurisdiction,personConfidence}=useReviewPreferences();
  const [busy,setBusy]=useState(false),[busyLabel,setBusyLabel]=useState(''),[error,setError]=useState('');
  const [preview,setPreview]=useState(''),[clock,setClock]=useState(0),[connected,setConnected]=useState(false);
  const [people,setPeople]=useState<LivePersonSample|null>(null);
  const [retained,setRetained]=useState<FrameRef|null>(null);
  const selectedRef=useRef(''),lock=useRef(false),mounted=useRef(true);
  const session=useRef<{id?:string|null;generation:number}>({generation:0});
  const video=useRef<HTMLVideoElement>(null);
  const current=status?.run.id===selected?status:null;

  async function library(){const rows=await request<RecordedSource[]>('/video-analysis/sources');if(mounted.current)setSources(rows);return rows;}
  function select(id:string){selectedRef.current=id;setSelected(id);setRetained(null);setPeople(null);setPreview('');setClock(0);setError('');}
  function update(next:ReviewRunStatus){setStatus(next);session.current={id:next.run.session_id,generation:next.run.generation??0};}
  useEffect(()=>{mounted.current=true;void library().catch(e=>setError(String(e)));return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let disposed=false,timer:ReturnType<typeof setTimeout>;
    async function load(){try{const rows=await request<ReviewRun[]>('/review-runs');if(!disposed)setRuns(rows);}catch(e){if(!disposed)setError(String(e));}if(!disposed)timer=setTimeout(load,2500);}
    void load();return()=>{disposed=true;clearTimeout(timer);};
  },[]);
  useEffect(()=>{
    let disposed=false,timer:ReturnType<typeof setTimeout>;
    setStatus(null);setCitations([]);
    if(!selected)return;
    async function load(){try{const next=await request<ReviewRunStatus>(`/review-runs/${selected}`);if(disposed)return;update(next);const refs=await request<SourceExcerpt[]>(`/review-runs/${selected}/sources`);if(!disposed)setCitations(refs);}catch(e){if(!disposed)setError(String(e));}if(!disposed)timer=setTimeout(load,1200);}
    void load();return()=>{disposed=true;clearTimeout(timer);};
  },[selected]);
  useEffect(()=>{
    if(kind!=='live')return;
    let disposed=false,socket:WebSocket,retry:ReturnType<typeof setTimeout>;
    function connect(){socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/ws`);
      socket.onopen=()=>{if(!disposed)setConnected(true);};
      socket.onmessage=e=>{try{const m=JSON.parse(e.data);if(!disposed&&m.type==='frame'&&m.frame.session_id===session.current.id&&m.frame.run_generation===session.current.generation){setPreview(`data:image/jpeg;base64,${m.jpeg}`);setClock(m.frame.timestamp_ms);}if(!disposed&&m.type==='review_people'&&m.sample.review_run_id===selectedRef.current&&m.sample.session_id===session.current.id&&m.sample.run_generation===session.current.generation){setPeople(m.sample);}}catch{/* Ignore unrelated socket events. */}};
      socket.onclose=()=>{if(!disposed){setConnected(false);retry=setTimeout(connect,1500);}};socket.onerror=()=>socket.close();}
    connect();return()=>{disposed=true;clearTimeout(retry);socket?.close();if(selectedRef.current)void request(`/review-runs/${selectedRef.current}/playback`,{action:'pause'}).catch(()=>{});};
  },[kind]);

  async function guarded(label:string,operation:()=>Promise<void>){
    if(lock.current)return false;lock.current=true;setBusy(true);setBusyLabel(label);setError('');
    try{await operation();return true;}catch(e){if(mounted.current)setError(e instanceof Error?e.message:String(e));return false;}
    finally{lock.current=false;if(mounted.current){setBusy(false);setBusyLabel('');}}
  }
  async function create(sourceId:string){
    if(!ai.status?.enabled||ai.busy)throw new Error(ai.status?.message??'Waiting for AI controls.');
    const body={source_id:sourceId,question:'Describe visible acute events first, including falling material, apparent contact and falls; then check PPE, access, stacks and slip/trip conditions. Cite retained evidence and disclose uncertainty.',review_strategy:'full',safety_screening:true,escalate_to_safety_team:true,mode,jurisdiction,required_ppe:ppe.split(',').map(v=>v.trim()).filter(Boolean),person_confidence:personConfidence,idempotency_key:crypto.randomUUID()};
    if(kind==='live'){
      const next=await request<ReviewRunStatus>('/review-runs/live',body);
      if(!mounted.current)return;
      select(next.run.id!);update(next);
      const playing=await request<ReviewRunStatus>(`/review-runs/${next.run.id}/playback`,{action:'play'});
      if(mounted.current&&selectedRef.current===next.run.id)update(playing);
      else await request(`/review-runs/${next.run.id}/playback`,{action:'pause'});
    }else{
      const job=await request<Job>('/video-analysis',body);
      if(mounted.current)select(job.review_run_id!);
    }
  }
  const start=(sourceId:string)=>guarded('Opening your video…',()=>create(sourceId));
  const upload=(file:File)=>guarded('Uploading your video…',async()=>{
    const before=await library();
    const response=await fetch(`/api/monitoring/uploads?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
    if(!response.ok){const result=await response.json();throw new Error(result.detail??'Could not upload this video.');}
    const after=await library();const added=after.find(s=>!before.some(old=>old.id===s.id));
    if(!added)throw new Error('The upload was saved, but could not be selected. Open it from your video library.');
    if(mounted.current){setBusyLabel('Starting your review…');await create(added.id);}
  });
  const control=(action:'play'|'pause'|'restart'|'cancel'|'resume')=>guarded('Updating review…',async()=>{
    const id=selectedRef.current;if(!id)return;
    const playback=['play','pause','restart'].includes(action);
    const next=await request<ReviewRunStatus>(`/review-runs/${id}/${playback?'playback':action}`,playback?{action}:{});
    if(!mounted.current||selectedRef.current!==id)return;
    if(next.run.id!==id)select(next.run.id!);
    if(action==='play'||action==='restart')setRetained(null);
    update(next);
    if(action==='restart'){
      const playing=await request<ReviewRunStatus>(`/review-runs/${next.run.id}/playback`,{action:'play'});
      if(mounted.current&&selectedRef.current===next.run.id)update(playing);
      else await request(`/review-runs/${next.run.id}/playback`,{action:'pause'});
    }
  });
  async function openRun(id:string){
    if(kind==='live'&&selectedRef.current&&id!==selectedRef.current){try{await request(`/review-runs/${selectedRef.current}/playback`,{action:'pause'});}catch(e){setError(String(e));return;}}
    select(id);
  }
  async function newVideo(){
    if(kind==='live'&&selectedRef.current){try{await request(`/review-runs/${selectedRef.current}/playback`,{action:'pause'});}catch(e){setError(String(e));return;}}
    select('');
  }
  async function inspect(item:VideoSafetyCaseView){
    if(video.current){video.current.currentTime=Math.max(0,issueInterval(item).start-(current?.run.start_ms??0))/1000;video.current.pause();return;}
    const id=selectedRef.current;
    if(kind==='live'&&current){try{
      const next=await request<ReviewRunStatus>(`/review-runs/${id}/playback`,{action:'pause'});
      const refs=await request<FrameRef[]>(`/media/${current.run.media_id}/frames`);
      if(selectedRef.current!==id||!mounted.current)return;
      update(next);const ref=item.screenings[0]?.check.evidence_refs[0];setRetained(refs.find(f=>f.id===ref)??null);
    }catch(e){setError(String(e));}}
  }
  const decide=(target:string,decision:'accepted'|'rejected'|'uncertain',reason:string)=>guarded('Saving your decision…',async()=>{
    const id=selectedRef.current;
    await request(`/review-runs/${id}/decisions`,{target_id:target,decision,reason});
    const next=await request<ReviewRunStatus>(`/review-runs/${id}`);if(mounted.current&&selectedRef.current===id)update(next);
  });
  const livePeople=liveSampleForDisplay(people,current?.run,clock,connected);
  return {livePeople,people,sources,runs,current,selected,citations,mode,ppe,setPpe,jurisdiction,setJurisdiction,busy,busyLabel,error,setError,preview,clock,connected,retained,video,library,start,upload,control,openRun,newVideo,inspect,decide};
}
