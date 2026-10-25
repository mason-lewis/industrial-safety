import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import LiveAnalysis from './LiveAnalysis';
import type {SceneReview,LabConfig} from '../types/contracts';
const review={id:'active',session_id:'s',run_generation:0,packet:{start_ms:5001,cutoff_ms:10000,trigger:'scheduled',refs:[]},analysis_mode:'ai_scene',status:'running',settings:{live_phase:'waiting_for_model',tool_trace:[{tool:'model_request',arguments:{attempt:1},result:'ok'}]},usage:{requests:1,input_tokens:20,output_tokens:0},received_images:[]} as unknown as SceneReview;
const render=(scene:unknown)=>renderToStaticMarkup(<LiveAnalysis scene={scene} session={{session_id:'s',source_id:'clip',run_generation:0}} reviews={[]} config={{review_budget_usd:.1} as LabConfig} enabled busy={false} onSave={async()=>true} onInspect={()=>{}} onError={()=>{}}/>);
describe('live processing view',()=>{
 it('shows real phases, request counts and editable applied budget',()=>{
  const html=render({live_review:review});
  expect(html).toContain('waiting for model');expect(html).toContain('1 requests');
  expect(html).toContain('model request');expect(html).toContain('Playback AI budget (USD)');
  expect(html).toContain('Apply budget');expect(html).toContain('$0.1000');
  expect(html).toContain('Inspect active review and images');
 });
 it('does not present another generation as the active or queued review',()=>{
  const html=render({live_review:{...review,run_generation:1},pending_review:{...review,session_id:'other'}});
  expect(html).toContain('No active review');expect(html).toContain('None queued');
  expect(html).not.toContain('Inspect active review and images');
 });
});
