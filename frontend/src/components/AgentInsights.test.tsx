import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {AgentInsights,UsageMonitor} from './AgentInsights';
import {agentActivity,chatUsage} from './agentTrace';
import type {AuditEvent} from '../api/generated';
const rows=[
 {id:'1',sequence:1,event_type:'model_request',run_id:'scene',agent_name:'legacy_review',status:'running',resources:{model_request_id:'req',reserved_usd:.005}},
 {id:'2',sequence:2,event_type:'tool_finished',run_id:'scene',agent_name:'legacy_scene',status:'ok',tool_name:'request_extra'},
 {id:'3',sequence:3,event_type:'model_response',run_id:'scene',agent_name:'legacy_review',status:'failed',error:{code:'ModelAPIError',message:'Connection error.'},resources:{model_request_id:'req'}},
 {id:'4',sequence:4,event_type:'legacy_record',run_id:'scene',status:'error',output:{status:'error'}},
] as unknown as AuditEvent[];
describe('video agent insights',()=>{
 it('uses actual requests and finished legacy tools with a terminal failure',()=>{
  const activity=agentActivity(rows);expect(activity).toHaveLength(1);expect(activity[0]).toMatchObject({requests:1,tools:1,status:'error'});
  const html=renderToStaticMarkup(<AgentInsights rows={rows} status="error" selected/>);
  expect(html).toContain('Video scene reviewer');expect(html).toContain('1 model requests');expect(html).toContain('1 tools');
 });
 it('retains unresolved costs instead of showing a known free failure',()=>{
  const usage=chatUsage(rows);expect(usage.reserved).toBe(.005);expect(usage.unknownCost).toBe(1);
  const html=renderToStaticMarkup(<UsageMonitor title="Tokens & cost · this review" usage={usage} selected fixture={false} partial={false}/>);
  expect(html).toContain('$0.0050');expect(html).toContain('Cost unresolved');expect(html).toContain('Tokens &amp; cost · this review');
 });
});
