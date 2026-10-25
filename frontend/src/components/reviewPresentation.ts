import type {LivePersonSample, ReviewRunStatus, VideoSafetyCaseView} from '../api/generated';

export const personCount = (count: number) => `${count} ${count === 1 ? 'person' : 'people'}`;

export const formatTime = (ms: number, precise=false) => {
  const value=Math.max(0,ms);
  const whole=`${Math.floor(value/60000)}:${Math.floor(value/1000%60).toString().padStart(2,'0')}`;
  return precise&&value%1000 ? `${whole}.${Math.round(value%1000).toString().padStart(3,'0').replace(/0+$/,'')}` : whole;
};
export const readable = (value: string) => value.replaceAll('_', ' ');
/** Library file name to a display title: drops the extension, numeric prefix and dashes. */
export const videoTitle = (name: string) => name.replace(/\.(mp4|mov|mkv|avi|webm|m4v)$/i, '').replace(/^\d+-/, '').replaceAll('-', ' ');
export const categoryName = (value: string) => ({ppe: 'Protective equipment', blocked_access: 'Access concern', unstable_stack: 'Unstable stack', slip_trip: 'Slip or trip hazard', struck_by: 'Struck-by concern', fall: 'Fall concern', fire_burn: 'Fire or burn concern'}[value] ?? readable(value));
export const issueState = (item: VideoSafetyCaseView) => {
  if (item.assessment) return item.unassessed_screening_ids?.length ? 'Partly reviewed'
    : item.assessment.findings?.length&&item.assessment.findings.every(f=>f.status==='challenged')?'Needs your review':'Team reviewed';
  if (item.status === 'running') return 'Team reviewing';
  if (['queued', 'awaiting_video_completion'].includes(item.status)) return 'Waiting for team';
  return 'Review unfinished';
};
export function issueDescription(item:VideoSafetyCaseView){
  const checked=item.assessment?.findings.find(f=>f.status==='reviewed'&&f.conclusion);
  if(checked)return checked.conclusion;
  if(item.assessment?.findings.some(f=>f.status==='challenged'))
    return 'This concern remains uncertain after the independent check. Open its evidence and qualifications.';
  return item.screenings[0]?.check.condition ?? 'Review the visual evidence for this concern.';
}
export function runHeadline(status: ReviewRunStatus) {
  if(status.run.state==='budget_exceeded'&&(status.windows??[]).some(w=>w.error?.includes('Image token preflight unavailable')))return 'Provider preflight unavailable · review unfinished';
  if (status.run.state === 'budget_exceeded') return 'Budget reached · review unfinished';
  if (status.run.state === 'failed' || status.run.state === 'interrupted') return 'Review interrupted';
  if (status.run.state === 'cancelled') return 'Stopped · results saved';
  if (status.run.state === 'paused') return 'Paused';
  if (status.run.kind === 'live' && status.run.state !== 'ended') return 'Watching for safety issues';
  if (!status.coverage_complete) return status.run.state === 'ended' ? 'Video ended · coverage incomplete' : 'Checking the recording';
  if (status.pending_specialists) return `${status.pending_specialists} team review${status.pending_specialists === 1 ? '' : 's'} unfinished`;
  return 'Review complete';
}
export function knownCost(usage: Record<string, unknown>) {
  const cost = usage.usage_cost_usd;
  return cost === null || cost === undefined ? 'Unknown' : `$${Number(cost).toFixed(4)}`;
}

// Episode bounds are grouping context. Navigation uses the actual observed interval.
export function issueInterval(item: VideoSafetyCaseView) {
  const checks=item.screenings.map(s=>s.check).filter(c=>c && Number.isFinite(c.start_ms) && Number.isFinite(c.end_ms)
    && c.start_ms>=item.case.start_ms && c.end_ms<=item.case.end_ms && c.end_ms>=c.start_ms);
  return checks.length ? {start:Math.min(...checks.map(c=>c.start_ms)),end:Math.max(...checks.map(c=>c.end_ms))}
    : {start:item.case.start_ms,end:item.case.end_ms};
}

// Acute events lead the feed after high-priority items.
const ACUTE=['struck_by','fall','fire_burn'];

// Presentation groups only. Every occurrence, assessment and human decision keeps its own ID.
export function groupIssues(cases: VideoSafetyCaseView[]) {
  const groups = new Map<string, VideoSafetyCaseView[]>();
  for (const item of cases) groups.set(item.case.category,[...(groups.get(item.case.category) ?? []),item]);
  return [...groups.entries()].map(([category,items])=>{
    items.sort((a,b)=>Number(b.priority==='high')-Number(a.priority==='high')||issueInterval(a).start-issueInterval(b).start);
    return {category,items,lead:items[0],start:Math.min(...items.map(i=>issueInterval(i).start)),end:Math.max(...items.map(i=>issueInterval(i).end)),
      observations:items.reduce((n,i)=>n+i.screenings.length,0)};
  }).sort((a,b)=>Number(b.lead.priority==='high')-Number(a.lead.priority==='high')||Number(ACUTE.includes(b.category))-Number(ACUTE.includes(a.category))||a.start-b.start);
}

export function liveSampleForDisplay(sample:LivePersonSample|null, run:ReviewRunStatus['run']|undefined, clock:number, connected:boolean) {
  if (!sample || !run || !connected || sample.status!=='available' || !sample.image_data_url ||
    sample.review_run_id!==run.id || sample.source_id!==run.source_id || sample.session_id!==run.session_id ||
    sample.run_generation!==run.generation || sample.timestamp_ms>clock || clock-sample.timestamp_ms>1500) return null;
  return sample;
}
