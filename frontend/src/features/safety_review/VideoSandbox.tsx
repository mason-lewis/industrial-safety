import {useEffect,useState} from 'react';
import {request} from '../../api/client';
import type {Job} from '../../api/generated';
import VideoAnalysis from './VideoAnalysis';

export default function VideoSandbox(){
 const [jobs,setJobs]=useState<Job[]>([]),[error,setError]=useState('');
 async function refresh(){setJobs(await request<Job[]>('/jobs'));setError('');}
 useEffect(()=>{
  let mounted=true;
  async function load(){try{const rows=await request<Job[]>('/jobs');if(mounted){setJobs(rows);setError('');}}catch(e){if(mounted)setError(String(e));}}
  void load();const timer=setInterval(load,2000);
  return()=>{mounted=false;clearInterval(timer);};
 },[]);
 return <main className="video-sandbox"><header><div><span className="sandbox-eyebrow">TEST &amp; VALIDATE</span><h1>Video sandbox</h1><p>Choose a video. Ask a question. Check the evidence.</p></div></header>{error&&<p className="error" role="alert">{error}</p>}<VideoAnalysis jobs={jobs} refresh={refresh}/></main>;
}
