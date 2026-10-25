import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {eventLabel,reviewUsage} from './aiActivityLogic';
import {AIReviewActivityView} from './AIReviewActivity';
import type {AuditEvent,LearningAIActivity} from './generated';
const event=(id:string,type:string,resources:Record<string,unknown>={},configuration:Record<string,unknown>={}):AuditEvent=>({id,event_type:type,status:'ok',resources,configuration});
describe('AI image review activity',()=>{
 it('counts actual requests/tools, deduplicates responses and retains unresolved cost',()=>{
  const request=event('r','model_request',{model_request_id:'one',reserved_usd:.003});
  const response=event('s','model_response',{model_request_id:'one',input_tokens:100,output_tokens:20,cost_usd:.001});
  const pending=event('r2','model_request',{model_request_id:'two',reserved_usd:.004});
  const usage=reviewUsage([request,request,response,response,pending,event('t','tool_started')]);
  expect(usage).toEqual({requests:2,tools:1,input:100,output:20,cost:.001,reserved:.004,unknown:1});
 });
 it('marks fixture calls free and gives model/validation stages plain names',()=>{
  expect(reviewUsage([event('r','model_request',{}, {fake_provider:true})]).unknown).toBe(0);
  expect(eventLabel(event('x','image_review_stage',{stage:'validating_boxes'}))).toContain('candidate decisions');
  expect(eventLabel(event('x','model_request'))).toBe('Image sent to model');
 });
 it('renders persisted failures, run selection and real tool counts without invented activity',()=>{
  const state:LearningAIActivity={attempts:[{id:'one',frame_id:'f',created_at:1,updated_at:2,revision:0,status:'blocked',stage:'checking_access_and_budget',model:'fixture',error:'Spending cap reached'}],selected_run_id:'one',audit:[event('x','run_failed')],partial:false};
  const html=renderToStaticMarkup(<AIReviewActivityView state={state} error="" selected="" onSelect={()=>{}}/>);
  for(const value of ['What the AI is doing','Spending cap reached','Its tools add boxes','0 tool calls','AI review run','audit events'])expect(html).toContain(value);
 });
});
