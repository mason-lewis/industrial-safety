import {describe,it,expect} from 'vitest';
import {agentActivity,chronological,chatUsage} from './chatTrace';
import type {AuditEvent} from '../../api/generated';
const row=(sequence:number,event_type:string,run_id:string,agent_name='specialist_vehicles',status='ok'):AuditEvent=>({sequence,event_type,run_id,agent_name,status});
describe('chat activity from actual audit rows',()=>{
 it('keeps separate specialist follow-up runs and ignores dispatch rows attributed to boss',()=>{
  const rows=[row(1,'run_started','boss','safety_reviewer','running'),row(2,'specialist_dispatched','boss'),row(3,'run_started','first'),row(4,'tool_started','first'),row(5,'model_request','first'),row(6,'run_finished','first'),row(7,'run_started','follow'),row(8,'run_failed','follow','specialist_vehicles','incomplete')];
  const nodes=agentActivity(rows.reverse());expect(nodes).toHaveLength(3);expect(nodes[0].name).toBe('safety_reviewer');expect(nodes[1]).toMatchObject({id:'first',tools:1,requests:1,status:'completed'});expect(nodes[2]).toMatchObject({id:'follow',status:'incomplete'});
 });
 it('sorts without mutating source rows',()=>{const rows=[row(3,'tool_finished','a'),row(1,'tool_started','a')];expect(chronological(rows).map(r=>r.sequence)).toEqual([1,3]);expect(rows[0].sequence).toBe(3);});
});
describe('chat turn usage',()=>{
 const event=(seq:number,kind:string,id:string,resources:Record<string,unknown>={},fixture=false):AuditEvent=>({...row(seq,kind,'run'),configuration:{fake_provider:fixture},resources:{model_request_id:id,...resources}});
 it('sums all attempts once, with active reservations separate from recorded charges',()=>{
  const request=event(1,'model_request','first',{reserved_usd:.01});
  const response=event(2,'model_response','first',{input_tokens:100,output_tokens:20,cost_usd:.003});
  const active=event(3,'model_request','retry',{reserved_usd:.02});
  const result=chatUsage([active,response,request,response]);
  expect(result).toMatchObject({requests:2,input:100,output:20,cost:.003,reserved:.02,inFlight:1,unknownCost:0});
  expect(result.agents.specialist_vehicles).toMatchObject({requests:2,input:100,output:20,cost:.003});
 });
 it('keeps failed usage and historical costs unknown instead of treating them as free',()=>{
  const result=chatUsage([event(1,'model_request','failed',{reserved_usd:.02}),event(2,'model_response','failed'),event(3,'model_request','old'),event(4,'model_response','old',{input_tokens:10,output_tokens:3})]);
  expect(result).toMatchObject({requests:2,reserved:.02,inFlight:0,unknownCost:2,unknownUsage:1});
 });
 it('exempts fixture costs and accepts responses whose request lies outside the visible trace',()=>{
  expect(chatUsage([event(1,'model_request','mock',{},true),event(2,'model_response','mock')])).toMatchObject({unknownCost:0,unknownUsage:0,reserved:0,cost:0});
  expect(chatUsage([event(2,'model_response','prior',{input_tokens:50,output_tokens:2,cost_usd:.001})])).toMatchObject({requests:1,input:50,output:2,cost:.001,unknownCost:0});
 });
});
