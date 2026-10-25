import {describe,it,expect} from 'vitest';
import type {AuditEvent} from '../../api/generated';
import {activityEnd,activityRows,activityDuration} from './reviewActivityEvents';

const event=(sequence:number,event_type:string,attempt:number,run_id='stage')=>({sequence,event_type,run_id,
 timestamp_utc:`2026-10-24T20:00:0${sequence}Z`,status:event_type.endsWith('started')?'running':'ok',
 resources:{payload_fingerprint:'same-payload',preflight_attempt:attempt}} as AuditEvent);

describe('recorded activity',()=>{
 it('finishes each read-only count attempt once without a phantom running timer',()=>{
  const first=event(1,'token_preflight_started',1),failed={...event(2,'token_preflight_failed',1),status:'failed'},
   second=event(3,'token_preflight_started',2),foreign=event(4,'token_preflight_completed',2,'other-stage'),
   done=event(5,'token_preflight_completed',2);
  const rows=[first,failed,second,foreign,done];
  expect(activityEnd(first,rows)).toBe(failed);expect(activityEnd(second,rows)).toBe(done);
  expect(activityRows(rows)).toEqual([first,second,foreign]);
  expect(activityDuration(second,done,Date.now())).toBe(2000);
 });
 it('does not match historical model events that have no request identity',()=>{
  const request=event(1,'model_request',1),response=event(2,'model_response',1);
  expect(activityRows([request,response])).toEqual([request,response]);
 });
});
