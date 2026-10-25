import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {needsAIDraft,runPreparation} from './preparationLogic';
import FramePreparation from './FramePreparation';
import type {ReviewFrame} from './FrameReview';
const frame:ReviewFrame={id:'one',clip_id:'clip',timestamp_ms:0,width:64,height:48,boxes:[],reviewed:false,verified_empty:false,revision:1};
describe('batch frame preparation',()=>{
 it('skips approved frames and current AI drafts, revisits stale drafts',()=>{
  expect(needsAIDraft(frame)).toBe(true);expect(needsAIDraft({...frame,reviewed:true})).toBe(false);
  const result={id:'r',created_at:1,revision:1,model:'fixture',image_sha256:'h',input_sha256:'h',result:{decisions:[],additions:[],limitations:[]},added_boxes:[]};
  expect(needsAIDraft({...frame,ai_review:result})).toBe(false);
  expect(needsAIDraft({...frame,revision:2,ai_review:result})).toBe(true);
 });
 it('stops at first failure, retains completed work and never retries paid requests',async()=>{
  const seen:number[]=[],progress:number[]=[];
  const result=await runPreparation([1,2,3],async i=>{seen.push(i);if(i===2)throw new Error('Budget blocked');},()=>false,d=>progress.push(d));
  expect(result).toEqual({completed:1,error:'Budget blocked'});expect(seen).toEqual([1,2]);expect(progress).toEqual([1]);
 });
 it('stops after current operation and makes no next submission',async()=>{
  let stop=false;const seen:number[]=[];
  const result=await runPreparation([1,2],async i=>{seen.push(i);stop=true;},()=>stop,()=>{});
  expect(result.completed).toBe(1);expect(seen).toEqual([1]);
 });
 it('renders whole-clip and every-frame choices, pending counts, review and budget boundaries',()=>{
  const html=renderToStaticMarkup(<FramePreparation projectId="p" clips={[{id:'clip',status:'ready',frames:1,split:'train'}]} frames={[frame]} frozen={false} busy={false} dirty={false} aiReady={true} role="all" onBusy={()=>{}} onAIWorking={()=>{}} onRefresh={async()=>{}} onFrame={()=>{}}/>);
  for(const value of ['Spread samples across the whole clip','Every decoded frame','Sample 1 clips','Prepare AI drafts for 1 frames','never trains or approves labels','recording metadata are excluded'])expect(html).toContain(value);
 });
});
