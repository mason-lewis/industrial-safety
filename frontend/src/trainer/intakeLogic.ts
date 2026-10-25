import type {LearningIntakeClip,LearningIntakeInput} from './generated';
export type IntakeRole='auto'|'train'|'valid'|'test';
export interface IntakeImportResult{id:string;ok:boolean;error?:string}
export function intakeFolder(c:LearningIntakeClip){const i=c.relative_path.lastIndexOf('/');return i<0?'Intake root':c.relative_path.slice(0,i);}
export function folderSorted(clips:LearningIntakeClip[]){return [...clips].sort((a,b)=>intakeFolder(a).localeCompare(intakeFolder(b),undefined,{numeric:true,sensitivity:'base'})||a.id.localeCompare(b.id));}
export function isImported(c:LearningIntakeClip){return !!c.imported_in?.length;}
export function selectAvailable(selected:string[],clips:LearningIntakeClip[]){return [...new Set([...selected,...clips.filter(c=>!isImported(c)).map(c=>c.id)])];}
export function batchInputs(clips:LearningIntakeClip[],selected:string[],role:IntakeRole,group?:string):LearningIntakeInput[]{
 const ids=new Set(selected);
 // Batch processing order is opaque, independent of the folder-sorted presentation.
 return clips.filter(c=>ids.has(c.id)&&!isImported(c)).sort((a,b)=>a.id.localeCompare(b.id)).map(c=>({intake_id:c.id,split:c.source_split==='test'?'test':role==='auto'?c.suggested_split:role,group}));
}
