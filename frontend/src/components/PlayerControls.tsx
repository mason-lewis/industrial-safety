import HelpTip,{HelpSetting} from './HelpTip';
import {help} from './settingsHelp';
import {useEffect,useRef,useState} from 'react';
import type {Session} from '../types/api';
import {clock,seekTarget,type PlaybackCommand} from './playback';
export function Icon({name}:{name:'play'|'pause'|'back'|'forward'|'restart'|'step'|'fullscreen'}){
 const paths={play:<path d="m9 5 11 7-11 7Z"/>,pause:<><path d="M8 5v14M16 5v14"/></>,back:<><path d="M5 8v5h5M5.5 12a8 8 0 1 1 2 6"/><text x="10" y="15" stroke="none" fill="currentColor" fontSize="8">10</text></>,forward:<><path d="M19 8v5h-5M18.5 12a8 8 0 1 0-2 6"/><text x="6" y="15" stroke="none" fill="currentColor" fontSize="8">10</text></>,restart:<><path d="M4 4v6h6M4.5 10a8 8 0 1 1 .5 7"/></>,step:<><path d="m5 5 10 7-10 7ZM19 5v14"/></>,fullscreen:<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>};
 return <svg className="player-icon" viewBox="0 0 24 24" fill={name==='play'?'currentColor':'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
interface Props{session:Session|null;timestamp:number;busy:boolean;onPlayback:(command:PlaybackCommand)=>Promise<void>;onFullscreen:()=>void;fullscreen:boolean;markers:{id:string;timestamp:number;label:string}[]}
export default function PlayerControls({session,timestamp,busy,onPlayback,onFullscreen,fullscreen,markers}:Props){
 const [drag,setDrag]=useState<number|null>(null),[scrubbing,setScrubbing]=useState(false);
 const dirty=useRef(false),resume=useRef(false);
 useEffect(()=>{dirty.current=false;resume.current=false;setDrag(null);setScrubbing(false);},[session?.session_id,session?.run_generation]);
 const duration=session?.duration_ms??null,isLive=session?.source_kind==='rtsp';
 const seekable=!!session&&!isLive&&!!duration;
 const value=drag??timestamp;
 const commit=()=>{if(!dirty.current){setScrubbing(false);setDrag(null);return;}dirty.current=false;setScrubbing(false);const target=seekTarget(value,duration);void onPlayback({action:'seek',timestamp_ms:target,resume:resume.current}).finally(()=>setDrag(null));};
 const jump=(amount:number)=>onPlayback({action:'seek',timestamp_ms:seekTarget(timestamp+amount,duration),resume:!!session?.playing});
 return <div className="player-controls"><div className="scrub-row">
 <HelpTip label="Video timeline">{help.timeline}</HelpTip><div className="scrub-track"><input aria-label="Video timeline" aria-valuetext={`${clock(value)} of ${duration?clock(duration):'unknown duration'}`} type="range" min="0" max={Math.max(1,duration??timestamp)} step="100" value={Math.min(value,duration??Math.max(1,timestamp))} disabled={!seekable||(busy&&!scrubbing)}
 style={{background:`linear-gradient(to right, #8ec8ff ${duration?100*value/duration:0}%, #354152 ${duration?100*value/duration:0}%)`}}
 onPointerDown={()=>{resume.current=!!session?.playing;setScrubbing(true);}}
 onChange={e=>{if(!scrubbing)resume.current=!!session?.playing;dirty.current=true;setDrag(Number(e.target.value));}}
 onPointerUp={commit} onPointerCancel={()=>{dirty.current=false;setDrag(null);setScrubbing(false);}}
 onKeyUp={e=>{if(['ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(e.key))commit();}} onBlur={commit}/>
 {seekable&&<div className="scrub-markers" aria-label="Event markers">{markers.filter(m=>m.timestamp<=duration!).map(m=><span key={m.id} style={{left:`${100*m.timestamp/duration!}%`}} title={`${m.label} · ${clock(m.timestamp)}`}/>)}</div>}
 </div><span className="scrub-preview" aria-live="off">{scrubbing?`Seek to ${clock(value)}`:isLive?'Live camera':busy?'Updating video…':''}</span></div>
 <div className="transport-row"><div className="transport-left"><HelpTip label="Playback controls">{help.transport}</HelpTip>
 <button className="icon-button main-play" aria-label={session?.playing?'Pause':'Play'} title={session?.playing?'Pause (Space)':'Play (Space)'} disabled={!session||busy} onClick={()=>onPlayback({action:session?.playing?'pause':'play'})}><Icon name={session?.playing?'pause':'play'}/></button>
 <button className="icon-button" aria-label="Back 10 seconds" title="Back 10 seconds" disabled={!seekable||busy} onClick={()=>jump(-10000)}><Icon name="back"/></button>
 <button className="icon-button" aria-label="Forward 10 seconds" title="Forward 10 seconds" disabled={!seekable||busy} onClick={()=>jump(10000)}><Icon name="forward"/></button>
 <span className="player-time">{clock(value)} <span>/ {isLive?'LIVE':duration?clock(duration):'--:--'}</span></span>
 </div><div className="transport-right">
 <HelpSetting label="Playback speed" help={help.speed}><label className="speed-control"><span className="sr-only">Playback speed</span><select aria-label="Playback speed" value={session?.speed??1} disabled={!session||busy} onChange={e=>onPlayback({action:'speed',speed:Number(e.target.value)})}>{[.25,.5,1,1.5,2,4].map(s=><option key={s} value={s}>{s}×</option>)}</select></label></HelpSetting>
 <button className="icon-button secondary-control" aria-label="Restart video" title="Restart video" disabled={!seekable||busy} onClick={()=>onPlayback({action:'restart',resume:!!session?.playing})}><Icon name="restart"/></button>
 <button className="icon-button secondary-control" aria-label="Next analysis frame" title="Next analysis frame" disabled={!session||busy} onClick={()=>onPlayback({action:'step'})}><Icon name="step"/></button>
 <button className="icon-button" aria-label={fullscreen?'Exit fullscreen':'Fullscreen'} title="Fullscreen (F)" onClick={onFullscreen}><Icon name="fullscreen"/></button>
 </div></div></div>;
}
