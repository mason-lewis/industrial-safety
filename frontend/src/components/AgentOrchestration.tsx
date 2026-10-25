import {useEffect,useRef,useState} from 'react';
import Icon from './ReviewIcon';
import {layout,roleNames,toolLabel,type Orchestration,type OrchNode,type Role} from './orchestration';
import './agentOrchestration.css';

const icons:Record<Role,Parameters<typeof Icon>[0]['name']>={coordinator:'settings',planner:'folder',observer:'eye',detail:'expand',verifier:'check',lead:'shield',specialist:'report',answer:'report',challenger:'alert',combiner:'report'};
const statusText:Record<OrchNode['status'],string>={queued:'Waiting',running:'Working',completed:'Done',failed:'Failed',blocked:'Blocked',cancelled:'Stopped'};
const feedIcon={start:'play',handoff:'arrow',tool:'settings',done:'check',fail:'alert'} as const;
const secs=(ms:number)=>ms<1000?`${Math.round(ms)}ms`:ms<60000?`${(ms/1000).toFixed(1)}s`:`${Math.floor(ms/60000)}m ${Math.round(ms%60000/1000)}s`;
const usd=(n:number)=>`$${n.toFixed(4)}`;

export default function AgentOrchestration({orchestration:o,now=Date.now(),title='Agent team',empty='No agent has started yet.'}:{orchestration:Orchestration;now?:number;title?:string;empty?:string}){
 const [picked,setPicked]=useState<string|null>(null),[follow,setFollow]=useState(true),[width,setWidth]=useState(900);
 const map=useRef<HTMLDivElement>(null);
 useEffect(()=>{const el=map.current;if(!el)return;const update=()=>setWidth(Math.max(320,el.clientWidth));const ob=new ResizeObserver(update);ob.observe(el);update();return()=>ob.disconnect();},[o.nodes.length>1]);
 const {positions,height}=layout(o,width);
 const latestRunning=[...o.nodes].filter(n=>n.status==='running'&&n.role!=='coordinator').sort((a,b)=>b.start-a.start)[0];
 const selectedId=follow&&o.running?latestRunning?.id??picked:picked;
 const selected=o.nodes.find(n=>n.id===selectedId&&n.role!=='coordinator');
 const span=Math.max(1,o.end-o.start);
 const agents=o.nodes.filter(n=>n.role!=='coordinator');
 const parallel=new Map<string,number>();
 for(const e of o.edges){const child=o.nodes.find(n=>n.id===e.to);if(child?.status==='running')parallel.set(e.from,(parallel.get(e.from)??0)+1);}
 return <section className={`orch${o.running?' is-live':''}`} aria-label={title}>
  <div className="orch-head">
   <h3>{title}</h3>
   <span className={`orch-state ${o.running?'is-live':''}`}><i/>{o.running?`${o.running} working`:agents.length?'Idle':'Not started'}</span>
   <label className="orch-follow"><input type="checkbox" checked={follow} onChange={e=>setFollow(e.target.checked)}/>Follow live</label>
  </div>
  {!agents.length?<p className="orch-empty">{empty}</p>:<>
   <div className="orch-stats">
    <span><small>Agent runs</small><strong>{o.totals.agents}</strong></span>
    <span><small>Model requests</small><strong>{o.totals.requests}</strong></span>
    <span><small>Tool calls</small><strong>{o.totals.tools}</strong></span>
    <span><small>Cost</small><strong>{usd(o.totals.cost)}</strong></span>
    <span><small>Elapsed</small><strong>{secs(span)}</strong></span>
   </div>
   <div className="orch-body">
    <div className="orch-map" ref={map} style={{height}}>
     <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} aria-hidden="true">
      {o.edges.map(e=>{const a=positions.get(e.from),b=positions.get(e.to);if(!a||!b)return null;
       const mid=(a.y+b.y)/2,d=`M${a.x} ${a.y+30} C${a.x} ${mid} ${b.x} ${mid} ${b.x} ${b.y-30}`;
       return <g key={e.from+e.to} className={e.active?'is-active':''}><path d={d}/>{e.active&&<circle r="5"><animateMotion dur="1.4s" repeatCount="indefinite" path={d}/></circle>}</g>;})}
     </svg>
     {o.nodes.map(n=>{const p=positions.get(n.id);if(!p)return null;
      const busy=parallel.get(n.id)??0;
      return <div key={n.id} className="orch-slot" style={{left:p.x,top:p.y,['--slot' as string]:`${Math.min(240,width/p.slots-16)}px`}}>
       <button type="button" className={`orch-node is-${n.status} role-${n.role}`} aria-pressed={selectedId===n.id}
        disabled={n.role==='coordinator'} onClick={()=>{setFollow(false);setPicked(n.id===picked?null:n.id);}} title={`${n.label} · ${statusText[n.status]}`}>
        <span className="orch-icon"><Icon name={icons[n.role]}/></span>
        <span className="orch-text"><strong>{n.label}</strong><small>{n.role==='coordinator'?statusText[n.status]:`${statusText[n.status]}${n.requests?` · ${n.requests} req`:''}${n.cost?` · ${usd(n.cost)}`:''}`}</small></span>
        {n.runs.length>1&&<span className="orch-count">×{n.runs.length}</span>}
        {busy>1&&<span className="orch-parallel">{busy} in parallel</span>}
       </button>
       {n.toolCounts.length>0&&<span className="orch-tools">{n.toolCounts.slice(0,3).map(([name,count])=><span key={name} className={n.activeTool===name?'is-active':''} title={`${name} · ${count} call${count===1?'':'s'}`}><Icon name="settings"/>{toolLabel(name)}{count>1?` ×${count}`:''}</span>)}{n.toolCounts.length>3&&<span>+{n.toolCounts.length-3}</span>}</span>}
      </div>;})}
    </div>
    <ol className="orch-feed" aria-label="Latest activity">{o.feed.map((f,i)=><li key={i} className={`is-${f.kind}`}><span><Icon name={feedIcon[f.kind]}/></span><div>{f.text}<small>{secs(Math.max(0,f.at-o.start))}</small></div></li>)}</ol>
   </div>
   <div className="orch-lanes" aria-label="Timeline">
    <div className="orch-scale"><span>0s</span><span>{secs(span/2)}</span><span>{secs(span)}</span></div>
    {agents.map(n=><div className="orch-lane" key={n.id}><span>{n.label}{n.runs.length>1?` ×${n.runs.length}`:''}</span><div>
     {n.runs.map(r=>{const end=r.end??(r.status==='running'||r.status==='queued'?now:r.start);
      return <i key={r.id} className={`is-${r.status}`} title={`${statusText[r.status]} · ${secs(end-r.start)}`} style={{left:`${(r.start-o.start)/span*100}%`,width:`max(4px,${(end-r.start)/span*100}%)`}}/>;})}
     {n.runs.flatMap(r=>r.requestTimes).map((t,i)=><b key={'r'+i} className="req" style={{left:`${(t-o.start)/span*100}%`}}/>)}
     {n.runs.flatMap(r=>r.calls).map(c=><b key={c.id} className={`tool${c.ok?'':' is-failed'}`} title={`${toolLabel(c.name)}${c.end?` · ${secs(c.end-c.start)}`:' · running'}`} style={{left:`${(c.start-o.start)/span*100}%`}}/>)}
    </div></div>)}
    <div className="orch-legend"><span><i className="is-running"/>Working</span><span><i className="is-completed"/>Done</span><span><i className="is-failed"/>Failed / blocked</span><span><b className="req"/>Model request</span><span><b className="tool"/>Tool call</span><span><i className="edge"/>Handoff</span></div>
   </div>
   {selected&&<div className="orch-detail">
    <div><strong>{selected.label}</strong><small>{roleNames[selected.role]} · {selected.runs.length} run{selected.runs.length===1?'':'s'} · {selected.requests} requests · {selected.tools} tool calls · {usd(selected.cost)}</small></div>
    <ol>{selected.runs.slice(-8).map(r=><li key={r.id}><span className={`orch-dot is-${r.status}`}/>{statusText[r.status]} · {secs((r.end??now)-r.start)}{r.requests?` · ${r.requests} req`:''}{r.cost?` · ${usd(r.cost)}`:''}{r.detail&&r.detail!==selected.label?` · ${r.detail}`:''}
     {r.calls.length>0&&<span className="orch-calls">{r.calls.map(c=><span key={c.id} className={c.end==null?'is-active':c.ok?'':'is-failed'}>{toolLabel(c.name)}{c.end!=null?` ${secs(c.end-c.start)}`:' …'}</span>)}</span>}</li>)}</ol>
   </div>}
  </>}
 </section>;
}
