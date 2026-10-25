import type {AuditEvent} from './generated';
export const stageNames:Record<string,string>={preparing_image:'Preparing a clean image',image_prepared:'Image and candidate boxes prepared',checking_access_and_budget:'Checking AI access and spending limits',waiting_for_model:'Waiting for the model to inspect the image',validating_boxes:'Checking classes, box coordinates, and candidate decisions',recommendations_saved:'Recommendations ready for your review',saved_result_reused:'Reusing saved recommendations'};
export function eventLabel(row:AuditEvent){
 if(row.event_type==='image_review_stage')return stageNames[String(row.resources?.stage)]??'Review progress';
 return ({run_started:'Review started',model_request:'Image sent to model',model_response:'Model response received',validation_failed:'Output validation failed',retry_scheduled:'Bounded retry scheduled',budget_reserved:'Cost reserved before request',budget_reconciled:'Usage and cost recorded',budget_blocked:'Spending or setup blocked the request',run_finished:'AI recommendations saved',run_failed:'Review stopped with an error',run_interrupted:'Review interrupted by restart',cache_hit:'Saved result reused',human_review_save_requested:'Your label save requested',human_review_saved:'Your review saved',human_review_save_failed:'Your label save failed',tool_started:'Tool call started',tool_finished:'Tool call completed'} as Record<string,string>)[row.event_type]??row.event_type;
}
export function reviewUsage(rows:AuditEvent[]){
 const requests=new Map<string,AuditEvent>(),responses=new Map<string,AuditEvent>();
 for(const row of rows){const key=String(row.resources?.model_request_id??row.id);if(row.event_type==='model_request')requests.set(key,row);if(row.event_type==='model_response')responses.set(key,row);}
 let input=0,output=0,cost=0,reserved=0,unknown=0;
 for(const [id,r] of requests){const response=responses.get(id),resources=response?.resources??{},fixture=r.configuration?.fake_provider===true;
  if(typeof resources.input_tokens==='number')input+=resources.input_tokens;
  if(typeof resources.output_tokens==='number')output+=resources.output_tokens;
  if(typeof resources.cost_usd==='number')cost+=resources.cost_usd;
  else if(!fixture){unknown++;if(typeof r.resources?.reserved_usd==='number')reserved+=r.resources.reserved_usd;}
 }
 return {requests:requests.size,tools:rows.filter(r=>r.event_type==='tool_started').length,input,output,cost,reserved,unknown};
}
