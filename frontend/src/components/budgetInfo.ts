import type {SceneReview} from '../types/contracts';
export const money=(n:number)=>`$${n.toFixed(4)}`;
export const budgetFields=[
 {key:'day_usd',label:'Daily limit (USD)',description:'Total across all AI work in this project. Resets at midnight UTC.'},
 {key:'month_usd',label:'Monthly limit (USD)',description:'Total across all AI work in this project. Resets on the first day of each month, UTC.'},
 {key:'workflow_usd',label:'Per workflow limit (USD)',description:'One complete review or chat turn, including its agents. A live scene review is one workflow.'},
 {key:'agent_usd',label:'Per agent run limit (USD)',description:'One agent run, including its tool follow-ups and retries. All applicable limits must fit the next request.'},
] as const;
export function reviewStatus(r:SceneReview){
 const error=(r.error??'').toLowerCase();
 if(r.status==='blocked_pricing'||error.includes('pricing'))return 'Missing price allowance';
 if(r.status==='blocked_media'||error.includes('image limit')||error.includes('media limit'))return 'Image / media limit';
 if(r.status==='blocked_request')return 'Request limit';
 if(r.status==='budget_exhausted')return 'Spending limit';
 return (r.status??'queued').replaceAll('_',' ');
}
