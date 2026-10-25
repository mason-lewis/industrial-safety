import type {AuditEvent} from '../api/generated';
export type AgentActivity={id:string;name:string;status:string;requests:number;tools:number;parent?:string|null};
export function agentActivity(rows:AuditEvent[]):AgentActivity[]{
 const agents=new Map<string,AgentActivity>();
 for(const row of [...rows].sort((a,b)=>(a.sequence??0)-(b.sequence??0))){
  if(!row.run_id||row.event_type==='specialist_dispatched')continue;
  let item=agents.get(row.run_id);
  if(!item){if(!row.agent_name)continue;item={id:row.run_id,name:row.agent_name,status:'running',requests:0,tools:0,parent:row.parent_run_id};agents.set(row.run_id,item);}
  if(row.event_type==='run_finished'||row.event_type==='specialist_completed')item.status='completed';
  if(row.event_type==='run_failed')item.status=row.status;
  if(row.event_type==='model_request')item.requests++;
  if(row.event_type==='tool_started'||(row.event_type==='tool_finished'&&row.agent_name==='legacy_scene'))item.tools++;
  if(row.event_type==='model_response'&&row.error)item.status=row.status;
  if(row.event_type==='legacy_record'&&row.output&&!Array.isArray(row.output)&&row.output.status){const status=String(row.output.status);if(!['queued','running'].includes(status))item.status=status==='reviewed'?'completed':status;}
 }
 return [...agents.values()];
}
export function chronological(rows:AuditEvent[]){return [...rows].sort((a,b)=>(a.sequence??0)-(b.sequence??0));}

export type ChatUsage={requests:number;input:number;output:number;cost:number;reserved:number;inFlight:number;unknownUsage:number;unknownCost:number;agents:Record<string,{input:number;output:number;cost:number;requests:number}>};
export function chatUsage(rows:AuditEvent[]):ChatUsage{
 const result:ChatUsage={requests:0,input:0,output:0,cost:0,reserved:0,inFlight:0,unknownUsage:0,unknownCost:0,agents:{}};
 const attempts=new Map<string,{request?:AuditEvent;response?:AuditEvent}>();
 for(const row of chronological(rows)){
  if(!['model_request','model_response'].includes(row.event_type))continue;
  const id=row.resources?.model_request_id;
  if(typeof id!=='string')continue;
  const attempt=attempts.get(id)??{};
  if(row.event_type==='model_request')attempt.request=row;else attempt.response=row;
  attempts.set(id,attempt);
 }
 const number=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0?v:null;
 for(const {request,response} of attempts.values()){
  result.requests++;
  const name=request?.agent_name??response?.agent_name??'Unknown agent';
  const agent=result.agents[name]??={input:0,output:0,cost:0,requests:0};agent.requests++;
  const fixture=request?.configuration?.fake_provider===true;
  const input=number(response?.resources?.input_tokens),output=number(response?.resources?.output_tokens),cost=number(response?.resources?.cost_usd);
  result.input+=input??0;result.output+=output??0;agent.input+=input??0;agent.output+=output??0;
  result.cost+=cost??0;agent.cost+=cost??0;
  if(!response)result.inFlight++;
  if(!fixture&&response&&(input===null||output===null))result.unknownUsage++;
  if(!fixture&&cost===null){
   const held=number(request?.resources?.reserved_usd);
   if(held!==null)result.reserved+=held;
   // Historical/unknown usage must never look like a known zero charge.
   if(response||held===null)result.unknownCost++;
  }
 }
 return result;
}
