import type {ReviewFrame} from './FrameReview';
export function needsAIDraft(frame:ReviewFrame){return !frame.reviewed&&(!frame.ai_review||frame.ai_review.revision!==(frame.revision??0));}
export async function runPreparation<T>(items:T[],work:(item:T)=>Promise<void>,stop:()=>boolean,progress:(done:number)=>void){
 let completed=0,error='';
 for(const item of items){if(stop())break;try{await work(item);completed++;progress(completed);}catch(e){error=e instanceof Error?e.message:String(e);break;}}
 return {completed,error};
}
