import {useEffect,useRef,useState} from 'react';
import {request} from '../../../api/client';
import type {VideoChatState,VideoChatTurn,VideoChatInput,LabTelemetry} from '../../../api/generated';
export const busyTurn=(turn:VideoChatTurn)=>['queued','running'].includes(turn.job.status??'queued');
export function useVideoChat(runId:string){
 const [state,setState]=useState<VideoChatState|null>(null),[events,setEvents]=useState<Record<string,LabTelemetry[]>>({});
 const [error,setError]=useState(''),[technical,setTechnical]=useState(''),[sending,setSending]=useState(false),[pending,setPending]=useState<VideoChatInput|null>(null),[connected,setConnected]=useState(true);
 const mounted=useRef(true),base=`/review-runs/${runId}/chat`,storage=`video-ask-pending:${runId}`;
 const merge=(turn:VideoChatTurn)=>setState(old=>old?{...old,turns:[...(old.turns??[]).filter(t=>t.id!==turn.id),turn].sort((a,b)=>(a.job.created_at??'').localeCompare(b.job.created_at??''))}:old);
 async function load(){const next=await request<VideoChatState>(base);if(mounted.current)setState(next);return next;}
 useEffect(()=>{mounted.current=true;void load().catch(e=>mounted.current&&(setError('Could not open this conversation. Reload the review to reconnect.'),setTechnical(String(e))));try{const stored=sessionStorage.getItem(storage);if(stored)setPending(JSON.parse(stored));}catch{/* Storage may be unavailable. */}return()=>{mounted.current=false;};},[runId]);
 const active=state?.turns?.find(busyTurn);
 useEffect(()=>{
  if(!active)return;
  const stream=new EventSource(`/api${base}/turns/${active.id}/events`);
  stream.onopen=()=>setConnected(true);stream.onerror=()=>setConnected(false);
  stream.addEventListener('activity',event=>{
   const row=JSON.parse((event as MessageEvent).data) as LabTelemetry;
   setEvents(old=>({...old,[active.id]:[...(old[active.id]??[]).filter(e=>e.sequence!==row.sequence),row].sort((a,b)=>a.sequence-b.sequence)}));
  });
  stream.addEventListener('settled',()=>{stream.close();setConnected(true);void load().catch(e=>{setError('The answer is saved, but the connection needs a refresh. Reopen this review.');setTechnical(String(e));});});
  // Status polling is read-only and recovers from a lost terminal stream event.
  const timer=window.setInterval(()=>void request<VideoChatTurn>(`${base}/turns/${active.id}`).then(t=>{if(mounted.current)merge(t);}).catch(()=>{}),4000);
  return()=>{stream.close();clearInterval(timer);};
 },[active?.id,base]);
 async function send(body:VideoChatInput){
  if(sending||active)return false;
  setPending(body);setSending(true);setError('');try{sessionStorage.setItem(storage,JSON.stringify(body));}catch{}
  try{const turn=await request<VideoChatTurn>(`${base}/turns`,body);if(mounted.current){merge(turn);setPending(null);try{sessionStorage.removeItem(storage);}catch{}}return true;}
  catch(e){if(mounted.current)(setError('The submission could not be confirmed. Your question was kept. Retry uses the same request ID.'),setTechnical(String(e)));return false;}
  finally{if(mounted.current)setSending(false);}
 }
 async function activity(id:string){try{const rows=await request<LabTelemetry[]>(`${base}/turns/${id}/activity`);if(mounted.current)setEvents(old=>({...old,[id]:rows}));}catch(e){setError('Could not refresh the review activity. Reopen this review to reconnect.');setTechnical(String(e));}}
 async function stop(id:string){try{await request(`${base}/turns/${id}/cancel`,{});await load();}catch(e){setError('Could not refresh the review activity. Reopen this review to reconnect.');setTechnical(String(e));}}
 return {state,events,error,technical,sending,pending,connected,active,send,activity,stop};
}
