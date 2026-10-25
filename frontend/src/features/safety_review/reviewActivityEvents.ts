import type {AuditEvent} from '../../api/generated';

export function activityEnd(start:AuditEvent,rows:AuditEvent[]):AuditEvent|undefined{
 const later=(e:AuditEvent)=>e.run_id===start.run_id&&(e.sequence??0)>(start.sequence??0);
 if(start.event_type==='model_request'&&start.resources?.model_request_id)
  return rows.find(e=>later(e)&&e.event_type==='model_response'&&e.resources?.model_request_id===start.resources?.model_request_id);
 if(start.event_type==='tool_started'&&start.tool_call_id)
  return rows.find(e=>later(e)&&e.event_type==='tool_finished'&&e.tool_call_id===start.tool_call_id);
 if(start.event_type==='token_preflight_started'&&start.resources?.payload_fingerprint)
  return rows.find(e=>later(e)&&['token_preflight_completed','token_preflight_failed'].includes(e.event_type)&&
   e.resources?.payload_fingerprint===start.resources?.payload_fingerprint&&e.resources?.preflight_attempt===start.resources?.preflight_attempt);
 return undefined;
}

export function activityRows(rows:AuditEvent[]):AuditEvent[]{
 const hidden=new Set(rows.map(r=>activityEnd(r,rows)).filter(Boolean));
 return rows.filter(r=>!hidden.has(r)&&['model_request','model_response','tool_started','tool_finished',
  'specialist_dispatched','specialist_completed','temporal_detail_started','temporal_detail_finished',
  'request_queued','request_queue_finished','token_preflight_started','token_preflight_completed','token_preflight_failed'].includes(r.event_type)).slice(-60);
}

export function activityDuration(start:AuditEvent,end:AuditEvent|undefined,now:number):number|null{
 const duration=end?.duration_ms??start.duration_ms;
 if(duration!=null)return duration;
 const begin=Date.parse(start.timestamp_utc??'');
 if(!Number.isFinite(begin))return null;
 const finish=end?Date.parse(end.timestamp_utc??''):start.status==='running'?now:NaN;
 return Number.isFinite(finish)?Math.max(0,finish-begin):null;
}
