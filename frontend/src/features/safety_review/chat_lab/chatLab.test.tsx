import {describe,it,expect} from 'vitest';
import {mergeEvents,nodesFrom} from './ChatLab';
import type {LabTelemetry} from '../../../api/generated';
const event=(sequence:number,type:string,state:LabTelemetry['state']='running'):LabTelemetry=>({sequence,timestamp_utc:'2026-10-24T00:00:00Z',event_type:type,state,branch:'optimized',node_id:'agent',agent:'Safety specialist',api:'responses'});
describe('live comparison activity',()=>{
 it('deduplicates replayed events and orders reconnect batches',()=>{expect(mergeEvents([event(3,'agent_finished'),event(1,'agent_started')],[event(2,'tool_started'),event(3,'agent_finished')]).map(e=>e.sequence)).toEqual([1,2,3]);});
 it('shows a running agent before completion and retains tool activity',()=>{const value=nodesFrom([event(1,'agent_started'),event(2,'tool_started')]);expect(value[0].state).toBe('running');expect(value[0].events).toHaveLength(2);});
 it('uses recorded completion time and failure state',()=>{const value=nodesFrom([event(1,'agent_started'),{...event(2,'agent_finished','failed'),duration_ms:1234}]);expect(value[0].state).toBe('failed');expect(value[0].duration).toBe(1234);});
 it('shows a completed reviewer that held the answer as blocked',()=>{const value=nodesFrom([event(1,'agent_started'),{...event(2,'agent_finished','completed'),duration_ms:1234},event(3,'challenge_checked','blocked')]);expect(value[0].state).toBe('blocked');expect(value[0].duration).toBe(1234);expect(value[0].events.at(-1)?.event_type).toBe('challenge_checked');});
 it('does not invent nodes from model output or unaffiliated events',()=>{expect(nodesFrom([event(1,'model_response'),{...event(2,'agent_started'),branch:null}])).toEqual([]);});
});

import {renderToStaticMarkup} from 'react-dom/server';
import {AnswerResult} from './ChatLab';
import type {LabBranchResult} from '../../../api/generated';
describe('reviewed answer blocks',()=>{
 it('renders labeled approved blocks, not a duplicate narrative, and retains collapsed limitations',()=>{
  const result={branch:'current',answer:{answer:'LEGACY DUPLICATE',block_rendered:true,outcome:'partial',claims:[{id:'g',kind:'general',text:'Keep clear of **active hazards**.',citation_ids:[]}],missing_context:['a','b','c','d']},recovery:{attempts:1,succeeded:true,calculated_usd:.002}} as LabBranchResult;
  const html=renderToStaticMarkup(<AnswerResult result={result}/>);
  expect(html).toContain('General guidance · not verified against retrieved sources');expect(html).toContain('<strong>active hazards</strong>');expect(html).not.toContain('LEGACY DUPLICATE');expect(html).toContain('1 more limitation');expect(html).toContain('One correction completed');
 });
 it('distinguishes a branch that never started from an independent failure',()=>{
  const result={branch:'optimized',not_started:true,answer:{answer:'The comparison stopped before this branch.',outcome:'unavailable'},issue_codes:['not_started']} as LabBranchResult;
  const html=renderToStaticMarkup(<AnswerResult result={result}/>);expect(html).toContain('NOT STARTED');expect(html).toContain('Technical details');expect(html).not.toContain('INCOMPLETE RESULT');
 });
});

import {Limitations} from './ChatLab';
it('paginates long limitation lists without discarding later entries',()=>{
 const html=renderToStaticMarkup(<Limitations items={Array.from({length:40},(_,i)=>`Gap ${i}`)}/>);
 expect(html).toContain('37 more limitations');expect(html).toContain('Page 1 of 8');expect(html).toContain('Next');expect(html).not.toContain('Gap 8</li>');
});
