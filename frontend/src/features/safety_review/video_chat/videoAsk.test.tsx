import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import VideoAsk,{progressStages} from './VideoAsk';
import {busyTurn} from './useVideoChat';
import type {LabTelemetry,VideoChatTurn} from '../../../api/generated';
const event=(sequence:number,agent:string,type='agent_started'):LabTelemetry=>({sequence,timestamp_utc:'2026-10-24T00:00:00Z',event_type:type,state:type==='agent_started'?'running':'completed',node_id:agent,agent,api:'responses'});
describe('review-bound Ask',()=>{
 it('renders a passive accessible composer without a paid submission on mount',()=>{
  const html=renderToStaticMarkup(<VideoAsk runId="review" mode="hosted" focus={{timestamp_ms:3200}} onClearFocus={()=>{}} onSeek={()=>{}}/>);
  expect(html).toContain('<h2>Ask</h2>');expect(html).toContain('Question about this video');expect(html).toContain('Remove question focus');expect(html).toContain('Up to $0.05 per question');expect(html).toContain('Audio is excluded');expect(html).toContain('store=false does not promise zero retention');expect(html).toContain('Export conversation');expect(html).not.toContain('openai_api_key');
 });
 it('only shows stages that actually ran, including correction',()=>{
  expect(progressStages([])).toEqual([]);
  expect(progressStages([event(1,'Find evidence'),event(2,'Find evidence','agent_finished'),event(3,'Inspect frames')])).toEqual([{label:'Find evidence',done:true},{label:'Inspect',done:false}]);
  expect(progressStages([event(1,'Correct answer blocks')])).toEqual([{label:'Verify',done:false}]);
 });
 it('tracks waiting work separately from settled unavailable answers',()=>{
  expect(busyTurn({job:{status:'queued'}} as VideoChatTurn)).toBe(true);
  expect(busyTurn({job:{status:'interrupted'},answer:{outcome:'unavailable'}} as VideoChatTurn)).toBe(false);
 });
 it('clearly labels fixture mode',()=>{const html=renderToStaticMarkup(<VideoAsk runId="review" mode="fixture" focus={{}} onClearFocus={()=>{}} onSeek={()=>{}}/>);expect(html).toContain('Demo · no charge');});
});
