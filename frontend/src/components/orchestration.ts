import type {AuditEvent,LabTelemetry} from '../api/generated';

// Role hierarchy used by the agent tree. Recorded runs are grouped by role, so a ten-minute
// review shows one "Video observer ×60" node instead of sixty cards.
export type Role='coordinator'|'planner'|'observer'|'detail'|'verifier'|'lead'|'specialist'|'answer'|'challenger'|'combiner';
export type NodeStatus='queued'|'running'|'completed'|'failed'|'blocked'|'cancelled';
export type ToolCall={id:string;name:string;start:number;end?:number;ok:boolean};
export type AgentRun={id:string;status:NodeStatus;start:number;end?:number;requests:number;tools:string[];calls:ToolCall[];requestTimes:number[];cost:number;detail?:string};
export type FeedItem={at:number;kind:'start'|'handoff'|'tool'|'done'|'fail';text:string};
export type OrchNode={id:string;role:Role;label:string;status:NodeStatus;runs:AgentRun[];requests:number;tools:number;cost:number;start:number;end?:number;toolCounts:[string,number][];activeTool?:string};
export type OrchEdge={from:string;to:string;active:boolean};
export type Orchestration={nodes:OrchNode[];edges:OrchEdge[];start:number;end:number;running:number;feed:FeedItem[];totals:{agents:number;requests:number;tools:number;cost:number}};

const order:Role[]=['coordinator','planner','observer','detail','verifier','lead','specialist','answer','challenger','combiner'];
const parents:Record<Role,Role[]>={coordinator:[],planner:['coordinator'],observer:['planner','coordinator'],detail:['observer'],verifier:['detail'],
 lead:['observer','planner','coordinator'],specialist:['lead'],answer:['lead','observer','planner','coordinator'],challenger:['answer','lead','observer','coordinator'],combiner:['challenger','lead','observer','coordinator']};
export const roleNames:Record<Role,string>={coordinator:'Coordinator',planner:'Planner',observer:'Video observer',detail:'Detail observer',verifier:'Visual verifier',
 lead:'Safety lead',specialist:'Specialist',answer:'Answer writer',challenger:'Independent challenger',combiner:'Team combiner'};
/** Readable tool names for the map. */
export const toolLabel=(name:string)=>name.replace(/^get_|^search_|^fetch_/,'').replaceAll('_',' ');
const rank:Record<NodeStatus,number>={running:5,queued:4,failed:3,blocked:3,cancelled:2,completed:1};
const titleCase=(s:string)=>s.charAt(0).toUpperCase()+s.slice(1);
const time=(s?:string|null)=>{const t=Date.parse(s??'');return Number.isFinite(t)?t:0;};
const money=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0?v:0;
const finalStatus=(s:string):NodeStatus=>s==='blocked'||s==='budget_exceeded'?'blocked':s==='cancelled'?'cancelled':'failed';

function reviewRole(row:AuditEvent):{role:Role;group:string;label:string}|null{
 const name=row.agent_name??'',why=row.provenance?.role;
 if(name==='video_observer')return why==='detail_observer'?{role:'detail',group:'detail',label:roleNames.detail}:{role:'observer',group:'observer',label:roleNames.observer};
 if(name==='challenge_reviewer')return why==='visual_verifier'?{role:'verifier',group:'verifier',label:roleNames.verifier}:{role:'challenger',group:'challenger',label:roleNames.challenger};
 if(name==='safety_reviewer')return {role:'lead',group:'lead',label:roleNames.lead};
 if(name==='team_combiner')return {role:'combiner',group:'combiner',label:roleNames.combiner};
 if(name.startsWith('specialist_')){const p=name.slice(11);return {role:'specialist',group:name,label:`${titleCase(p)} specialist`};}
 return null;
}

function assemble(groups:Map<string,OrchNode>,coordinator:string,links:number[],now:number,feed:FeedItem[]=[]):Orchestration{
 const nodes=[...groups.values()];
 for(const n of nodes){
  n.status=n.runs.reduce<NodeStatus>((best,r)=>rank[r.status]>rank[best]?r.status:best,'completed');
  n.requests=n.runs.reduce((s,r)=>s+r.requests,0);n.tools=n.runs.reduce((s,r)=>s+r.tools.length,0);n.cost=n.runs.reduce((s,r)=>s+r.cost,0);
  const counts=new Map<string,number>();for(const r of n.runs)for(const c of r.calls)counts.set(c.name,(counts.get(c.name)??0)+1);
  n.toolCounts=[...counts].sort((a,b)=>b[1]-a[1]);
  n.activeTool=n.runs.flatMap(r=>r.calls).filter(c=>c.end==null).at(-1)?.name;
  n.start=Math.min(...n.runs.map(r=>r.start));const ends=n.runs.map(r=>r.end);n.end=ends.every(e=>e!=null)?Math.max(...(ends as number[])):undefined;
 }
 nodes.sort((a,b)=>order.indexOf(a.role)-order.indexOf(b.role)||a.start-b.start);
 const starts=nodes.map(n=>n.start).filter(Boolean),start=starts.length?Math.min(...starts):now;
 const running=nodes.filter(n=>n.status==='running').length;
 const root:OrchNode={id:'coordinator',role:'coordinator',label:coordinator,status:running?'running':nodes.some(n=>n.status==='queued')?'queued':nodes.length?'completed':'queued',runs:[],requests:0,tools:0,cost:0,start,toolCounts:[]};
 const all=[root,...nodes];
 const recent=links.some(t=>now-t<3000);
 const edges:OrchEdge[]=nodes.map(n=>{
  const parent=parents[n.role].map(r=>all.find(m=>m.role===r)).find(Boolean)??root;
  return {from:parent.id,to:n.id,active:n.status==='running'||(recent&&now-n.start<3000)};
 });
 const end=Math.max(start,...all.map(n=>n.end??(n.status==='running'?now:n.start)));
 const totals={agents:nodes.reduce((s,n)=>s+n.runs.length,0),requests:nodes.reduce((s,n)=>s+n.requests,0),tools:nodes.reduce((s,n)=>s+n.tools,0),cost:nodes.reduce((s,n)=>s+n.cost,0)};
 return {nodes:all,edges,start,end,running,feed:feed.slice(-8).reverse(),totals};
}

/** Live/Review runs: audit events from /api/review-runs/{id}/activity. */
export function buildOrchestration(rows:AuditEvent[],coordinator='Review coordinator',now=Date.now()):Orchestration{
 const groups=new Map<string,OrchNode>(),runs=new Map<string,AgentRun>(),links:number[]=[],feed:FeedItem[]=[],labels=new Map<string,string>();
 for(const row of [...rows].sort((a,b)=>(a.sequence??0)-(b.sequence??0))){
  if(['specialist_dispatched','video_safety_handoff','temporal_detail_started'].includes(row.event_type)){links.push(time(row.timestamp_utc));
   const to=row.event_type==='specialist_dispatched'?reviewRole({...row,event_type:'run_started'})?.label:row.event_type==='video_safety_handoff'?'Safety team':'Detail check';
   feed.push({at:time(row.timestamp_utc),kind:'handoff',text:`${row.event_type==='specialist_dispatched'?'Safety lead':'Coordinator'} → ${to}`});}
  if(!row.run_id)continue;
  if(row.event_type==='run_started'){
   const role=reviewRole(row);if(!role)continue;
   const run:AgentRun={id:row.run_id,status:'running',start:time(row.timestamp_utc),requests:0,tools:[],calls:[],requestTimes:[],cost:0,
    detail:typeof row.provenance?.call_id==='string'?`Delegation ${row.provenance.call_id.replace('delegation-','#')}`:undefined};
   runs.set(row.run_id,run);links.push(run.start);labels.set(row.run_id,role.label);feed.push({at:run.start,kind:'start',text:`${role.label} started`});
   const group=groups.get(role.group)??{id:role.group,role:role.role,label:role.label,status:'running',runs:[] as AgentRun[],requests:0,tools:0,cost:0,start:run.start,toolCounts:[]} as OrchNode;
   group.runs.push(run);groups.set(role.group,group);continue;
  }
  const run=runs.get(row.run_id);if(!run)continue;
  const who=labels.get(row.run_id)??'Agent',at=time(row.timestamp_utc);
  if(row.event_type==='run_finished'){run.status='completed';run.end=at;feed.push({at,kind:'done',text:`${who} finished`});}
  else if(row.event_type==='run_failed'){run.status=finalStatus(row.status);run.end=at;feed.push({at,kind:'fail',text:`${who} ${run.status==='blocked'?'blocked':'failed'}`});}
  else if(row.event_type==='model_request'){run.requests++;run.requestTimes.push(at);}
  else if(row.event_type==='model_response')run.cost+=money(row.resources?.cost_usd??row.resources?.usage_cost_usd);
  else if(row.event_type==='tool_started'&&row.tool_name){run.tools.push(row.tool_name);run.calls.push({id:row.tool_call_id??String(row.sequence),name:row.tool_name,start:at,ok:true});feed.push({at,kind:'tool',text:`${who} · ${toolLabel(row.tool_name)}`});}
  else if(row.event_type==='tool_finished'){const call=run.calls.find(c=>c.id===row.tool_call_id&&c.end==null);if(call){call.end=at;call.ok=row.status!=='error'&&!row.error;}}
 }
  return assemble(groups,coordinator,links,now,feed);
}

function askRole(label:string):{role:Role;group:string;label:string}{
 if(/specialist/i.test(label))return {role:'specialist',group:label,label};
 if(/team coordination/i.test(label))return {role:'lead',group:'lead',label:roleNames.lead};
 if(/inspect/i.test(label))return {role:'observer',group:'observer',label:'Frame inspector'};
 if(/explain/i.test(label))return {role:'answer',group:'answer',label:roleNames.answer};
 if(/verif|challenge|correct|recheck|final scope/i.test(label))return {role:'challenger',group:'challenger',label:'Verifier'};
 return {role:'planner',group:'planner',label:'Planner'};
}

/** Ask turns: normalized telemetry from the video-chat activity stream. */
export function buildAskOrchestration(events:LabTelemetry[],now=Date.now(),settled=false):Orchestration{
 const groups=new Map<string,OrchNode>(),runs=new Map<string,AgentRun>(),links:number[]=[],feed:FeedItem[]=[],labels=new Map<string,string>();
 for(const e of [...events].sort((a,b)=>a.sequence-b.sequence)){
  if(e.event_type==='handoff'){links.push(time(e.timestamp_utc));feed.push({at:time(e.timestamp_utc),kind:'handoff',text:`Handoff → ${askRole(e.agent??'').label}`});}
  if(!e.node_id)continue;
  if(e.event_type==='agent_started'){
   const role=askRole(e.agent??'');
   const run:AgentRun={id:e.node_id,status:'running',start:time(e.timestamp_utc),requests:0,tools:[],calls:[],requestTimes:[],cost:0,detail:e.agent??undefined};
   runs.set(e.node_id,run);labels.set(e.node_id,role.label);
   const group=groups.get(role.group)??{id:role.group,role:role.role,label:role.label,status:'running',runs:[] as AgentRun[],requests:0,tools:0,cost:0,start:run.start,toolCounts:[]} as OrchNode;
   group.runs.push(run);groups.set(role.group,group);continue;
  }
  const run=runs.get(e.node_id);if(!run)continue;
  const who=labels.get(e.node_id)??'Agent',at=time(e.timestamp_utc);
  if(e.event_type==='agent_finished'){run.status=e.state==='completed'?'completed':finalStatus(e.state);run.end=at;feed.push({at,kind:run.status==='completed'?'done':'fail',text:`${who} ${run.status==='completed'?'finished':run.status}`});}
  else if(e.event_type==='request_queued'&&run.status==='running'&&!run.requests)run.status='queued';
  else if(e.event_type==='request_queue_finished'&&run.status==='queued')run.status='running';
  else if(e.event_type==='model_request'){run.requests++;run.requestTimes.push(at);}
  else if(e.event_type==='model_response'){if(!run.requests){run.requests=1;run.requestTimes.push(at);}run.cost+=money(e.resources?.usage_cost_usd??e.resources?.cost_usd);}
  else if((e.event_type==='tool_started'||e.event_type==='native_tool_started')&&e.tool){run.tools.push(e.tool);run.calls.push({id:e.tool_call_id??String(e.sequence),name:e.tool,start:at,ok:true});feed.push({at,kind:'tool',text:`${who} · ${toolLabel(e.tool)}`});}
  else if(e.event_type==='tool_finished'||e.event_type==='native_tool_finished'){const call=run.calls.find(c=>c.id===e.tool_call_id&&c.end==null);if(call){call.end=at;call.ok=e.state!=='failed';}}
 }
 // A settled turn has no running agents; anything left open was interrupted.
 if(settled)for(const r of runs.values()){if(r.status==='running'||r.status==='queued'){r.status='cancelled';r.end=r.end??now;}for(const c of r.calls)c.end=c.end??r.end;}
 return assemble(groups,'Ask coordinator',links,now,feed);
}

/** Top-down layout on a 1000-unit-wide canvas: one row per tree depth. */
export function layout(o:Orchestration,width=1000,rowHeight=150){
 const depth=new Map<string,number>([['coordinator',0]]);
 for(let i=0;i<o.nodes.length;i++)for(const e of o.edges){const d=depth.get(e.from);if(d!=null&&!depth.has(e.to))depth.set(e.to,d+1);}
 const rows=new Map<number,OrchNode[]>();
 for(const n of o.nodes){const d=depth.get(n.id)??1;rows.set(d,[...(rows.get(d)??[]),n]);}
 // Crowded tree levels wrap onto extra lines so every agent card keeps a readable width.
 const perLine=Math.max(2,Math.floor(width/170));
 const positions=new Map<string,{x:number;y:number;slots:number}>();
 let line=0;
 for(const d of [...rows.keys()].sort((a,b)=>a-b)){
  const items=rows.get(d)!;
  for(let start=0;start<items.length;start+=perLine){
   const chunk=items.slice(start,start+perLine);
   chunk.forEach((n,i)=>positions.set(n.id,{x:(i+0.5)*width/chunk.length,y:line*rowHeight+52,slots:chunk.length}));
   line++;
  }
 }
 return {positions,height:Math.max(1,line)*rowHeight};
}
