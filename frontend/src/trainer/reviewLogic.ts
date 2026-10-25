import type {LearningBox,LearningProposal,LearningLabelInput,LearningAIReview} from './generated';
export type Decisions=NonNullable<LearningLabelInput['proposal_decisions']>;
export function reviewBoxes(saved:LearningBox[],proposals:LearningProposal[],decisions:Decisions):LearningBox[]{
 const ids=new Set(saved.map(b=>b.id));
 return [...saved,...proposals.filter(b=>!ids.has(b.id)&&decisions[b.id]!=='rejected').map(({id,label,bbox})=>({id,label,bbox}))];
}
export function acceptRemaining(boxes:LearningBox[],proposals:LearningProposal[],decisions:Decisions):Decisions{
 const next={...decisions},visible=new Set(boxes.map(b=>b.id));
 for(const b of proposals)if(!next[b.id])next[b.id]=visible.has(b.id)?'accepted':'rejected';
 return next;
}
export function nextUnreviewed(frames:{reviewed:boolean}[],current:number):number{
 for(let offset=1;offset<=frames.length;offset++){const index=(current+offset)%frames.length;if(!frames[index].reviewed)return index;}
 return current;
}
export function applyAIReview(boxes:LearningBox[],proposals:LearningProposal[],decisions:Decisions,review:LearningAIReview){
 const next=boxes.map(b=>({...b})),resolved={...decisions};
 for(const d of review.result.decisions){
  const i=next.findIndex(b=>b.id===d.target_id);if(i<0)continue;
  if(d.decision==='reject'){next.splice(i,1);if(proposals.some(p=>p.id===d.target_id))resolved[d.target_id]='rejected';}
  else if(d.decision==='adjust'&&d.replacement){next[i]={id:d.target_id,...d.replacement};if(proposals.some(p=>p.id===d.target_id))resolved[d.target_id]='adjusted';}
  else if(d.decision==='accept'){
   const original=proposals.find(p=>p.id===d.target_id);
   if(original)resolved[d.target_id]=original.label===next[i].label&&JSON.stringify(original.bbox)===JSON.stringify(next[i].bbox)?'accepted':'adjusted';
  }
 }
 const ids=new Set(next.map(b=>b.id));for(const b of review.added_boxes)if(!ids.has(b.id)){next.push(b);ids.add(b.id);}
 return {boxes:next,decisions:resolved};
}
