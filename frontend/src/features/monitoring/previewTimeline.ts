import type {PersonDetection,PersonPreview} from '../../api/generated';

export const overlayMaxAge = 500;
type Box = PersonDetection['box'];
function overlap(a: Box,b: Box) {
  const area = Math.max(0,Math.min(a[0]+a[2],b[0]+b[2])-Math.max(a[0],b[0]))
    * Math.max(0,Math.min(a[1]+a[3],b[1]+b[3])-Math.max(a[1],b[1]));
  return area / Math.max(Number.EPSILON,a[2]*a[3]+b[2]*b[3]-area);
}
function bestMatch(scores: number[]) {
  const ranked = scores.map((score,index)=>({score,index})).sort((a,b)=>b.score-a.score);
  // Only blend a clear spatial match. IDs are confidence ranks, not identities.
  return ranked[0]?.score >= .3 && ranked[0].score-(ranked[1]?.score ?? 0) >= .1 ? ranked[0].index : -1;
}
export function overlayAt(samples: PersonPreview[], source: string, confidence: number, position: number) {
  const frames = samples.filter(s=>s.source_id === source && s.status === 'available'
    && s.confidence_threshold === confidence).sort((a,b)=>a.position_ms-b.position_ms);
  const before = frames.filter(s=>s.position_ms <= position).at(-1);
  if (!before || position-before.position_ms > overlayMaxAge) return null;
  const after = frames.find(s=>s.position_ms > position);
  if (!after || after.position_ms-before.position_ms > overlayMaxAge
    || after.provenance?.input_width !== before.provenance?.input_width
    || after.provenance?.input_height !== before.provenance?.input_height) return before;
  const earlier = before.people ?? [], later = after.people ?? [];
  const scores = earlier.map(a=>later.map(b=>overlap(a.box,b.box)));
  const progress = (position-before.position_ms)/(after.position_ms-before.position_ms);
  // Display-only interpolation. Never add a person before their observed frame,
  // extrapolate motion, or modify detector scores/evidence.
  return {...before,people:earlier.map((person,index)=>{
    const match = bestMatch(scores[index]);
    if (match < 0 || bestMatch(scores.map(row=>row[match])) !== index) return person;
    return {...person,box:person.box.map((value,axis)=>value+(later[match].box[axis]-value)*progress) as Box};
  })};
}

export function nextOverlayPosition(position: number, duration: number, paused: boolean, previous: number) {
  const lookahead = previous >= position-100 ? 250 : 600;
  const target = Math.min(duration, paused ? position : Math.max(previous+250, position+lookahead));
  if (target > position+1200 || Math.abs(target-previous) < 80) return null;
  return Math.round(target);
}
