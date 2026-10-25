import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {reviewBoxes,acceptRemaining,nextUnreviewed,applyAIReview} from './reviewLogic';
import FrameReview from './FrameReview';
import type {LearningProposal,LearningAIReview} from './generated';
const proposals:LearningProposal[]=[{id:'p',label:'person',bbox:[.1,.1,.2,.3],score:.9},{id:'t',label:'truck',bbox:[.5,.5,.2,.2],score:.8}];
describe('guided object review',()=>{
 it('applies AI correction, rejection and additions to a draft without changing original labels',()=>{
  const review:LearningAIReview={id:'ai',revision:0,created_at:1,model:'fixture',image_sha256:'hash',input_sha256:'input',added_boxes:[{id:'new',label:'pallet',bbox:[.7,.7,.1,.1]}],result:{decisions:[{target_id:'p',decision:'adjust',reason:'wrong class',replacement:{label:'forklift',bbox:[.1,.1,.3,.3]}},{target_id:'t',decision:'reject',reason:'duplicate'}]}};
  const draft=applyAIReview(proposals,proposals,{},review);
  expect(draft.decisions).toEqual({p:'adjusted',t:'rejected'});
  expect(draft.boxes.map(b=>b.label)).toEqual(['forklift','pallet']);
  expect(proposals[0].label).toBe('person');expect(proposals).toHaveLength(2);
  expect(applyAIReview(draft.boxes,proposals,draft.decisions,review).boxes).toHaveLength(2);
 });
 it('preserves human adjustments when AI accepts them and leaves uncertainty unresolved',()=>{
  const review:LearningAIReview={id:'ai',revision:1,created_at:1,model:'fixture',image_sha256:'hash',input_sha256:'input',added_boxes:[],result:{decisions:[{target_id:'p',decision:'accept',reason:'visible'},{target_id:'t',decision:'uncertain',reason:'occluded'}]}};
  const draft=applyAIReview([{...proposals[0],label:'forklift'},proposals[1]],proposals,{},review);
  expect(draft.decisions).toEqual({p:'adjusted'});expect(draft.boxes).toHaveLength(2);
 });
 it('retains adjustments, omits rejections and does not duplicate proposals',()=>{
  const changed={id:'p',label:'person',bbox:[.2,.1,.2,.3] as [number,number,number,number]};
  expect(reviewBoxes([changed],proposals,{p:'adjusted',t:'rejected'})).toEqual([changed]);
 });
 it('accepts remaining visible suggestions without changing prior decisions',()=>{
  expect(acceptRemaining([proposals[0]],proposals,{p:'adjusted',t:'rejected'})).toEqual({p:'adjusted',t:'rejected'});
  expect(acceptRemaining(proposals,proposals,{})).toEqual({p:'accepted',t:'accepted'});
 });
 it('advances to the next unreviewed frame and wraps without leaving the collection',()=>{
  expect(nextUnreviewed([{reviewed:false},{reviewed:true},{reviewed:false}],0)).toBe(2);
  expect(nextUnreviewed([{reviewed:false},{reviewed:true},{reviewed:false}],2)).toBe(0);
  expect(nextUnreviewed([{reviewed:true}],0)).toBe(0);
 });
 it('renders blind review actions, missing-class limits and neutral image text',()=>{
  const html=renderToStaticMarkup(<FrameReview projectId="fixture" classes={['person','truck','forklift']} frozen={false} busy={false} frame={{id:'frame',clip_id:'clip',timestamp_ms:100,width:640,height:480,boxes:[],proposals,reviewed:false,verified_empty:false,proposal_run:{unsupported_classes:['forklift'],method:'local_detector',confidence:.25}}} onDirty={()=>{}} onSave={async()=>{}}/>);
  for(const text of ['Accept','Reject','Adjust','Add missed object','Accept remaining','forklift','Titles and behavior labels are hidden'])expect(html).toContain(text);
  expect(html).not.toContain('unsafe');expect(html).not.toContain('source_category');expect(html).toContain('Review frame at 0.10 seconds');
 });
});
