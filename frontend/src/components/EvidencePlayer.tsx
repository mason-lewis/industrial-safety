import {useEffect,useState} from 'react';
import {request} from '../api/client';
import type {FrameRef} from '../api/generated';
export default function EvidencePlayer({mediaId}:{mediaId:string}){
 const [frames,setFrames]=useState<FrameRef[]>([]),[index,setIndex]=useState(0),[playing,setPlaying]=useState(false),[error,setError]=useState('');
 useEffect(()=>{setIndex(0);setPlaying(false);let active=true;request<FrameRef[]>(`/media/${encodeURIComponent(mediaId)}/frames`).then(v=>{if(active)setFrames(v.sort((a,b)=>a.timestamp_ms-b.timestamp_ms));}).catch(e=>{if(active)setError(String(e));});return()=>{active=false;};},[mediaId]);
 useEffect(()=>{if(!playing)return;const timer=setInterval(()=>setIndex(v=>{if(v>=frames.length-1){setPlaying(false);return v;}return v+1;}),500);return()=>clearInterval(timer);},[playing,frames.length]);
 const f=frames[index];return <section className="panel evidence-player"><h3>Retained evidence</h3>{error&&<p role="alert">{error}</p>}{f?<><img src={f.image_url} alt={`Source frame at ${f.timestamp_ms} milliseconds`} onError={()=>setError('Evidence is missing. This interval is unknown.')}/><p>{(f.timestamp_ms/1000).toFixed(2)} source seconds · frame {f.frame_id}</p><div className="toolbar"><button onClick={()=>setPlaying(!playing)}>{playing?'Pause samples':'Play samples'}</button><input aria-label="Evidence frame" type="range" min="0" max={Math.max(0,frames.length-1)} value={index} onChange={e=>{setPlaying(false);setIndex(Number(e.target.value));}}/></div></>:<p>No retained frames. Coverage is unknown.</p>}<p className="muted">This player shows sampled original frames. Motion between frames is unknown.</p></section>;
}
