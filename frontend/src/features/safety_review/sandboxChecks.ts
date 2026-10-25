export type Verdict='matches'|'does_not_match'|'unsure';
export type SandboxCheck={jobId:string;analysisId:string;verdict:Verdict;notes:string;createdAt:string};
const prefix='video-sandbox-checks-v1:';
const verdicts:Verdict[]=['matches','does_not_match','unsure'];

export function readChecks(storage:Pick<Storage,'getItem'>,jobId:string):SandboxCheck[]{
 const value=storage.getItem(prefix+jobId);
 if(!value)return [];
 const rows:unknown=JSON.parse(value);
 if(!Array.isArray(rows)||rows.some(row=>!row||typeof row!=='object'||row.jobId!==jobId||typeof row.analysisId!=='string'||!verdicts.includes(row.verdict)||typeof row.notes!=='string'||typeof row.createdAt!=='string'))throw new Error('Saved checks could not be read. Existing browser data was kept.');
 return rows;
}

export function saveCheck(storage:Pick<Storage,'getItem'|'setItem'>,check:SandboxCheck):SandboxCheck[]{
 if(!check.jobId||!check.analysisId||!verdicts.includes(check.verdict))throw new Error('Choose a completed test and a valid check.');
 const rows=[...readChecks(storage,check.jobId),check];
 storage.setItem(prefix+check.jobId,JSON.stringify(rows));
 return rows;
}
