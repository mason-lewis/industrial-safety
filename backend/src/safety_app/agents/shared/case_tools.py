"""Read-only, deterministic tools for the review team: prior site history and person-box motion.

Neither tool makes a model call, decodes video or runs new inference. Outputs are context for judgment,
not new visual evidence: findings still cite the original frames and sources.
"""
from typing import Annotated,Literal
from pydantic import Field
from pydantic_ai import RunContext
from safety_app.agents.shared.deps import AgentDeps
from safety_app.agents.shared.evidence_refs import resolve_frame
from safety_app.agents.shared.tools import audited
from safety_app.domain.base import StrictModel

Category=Literal['ppe','blocked_access','unstable_stack','slip_trip','struck_by','fall','fire_burn']


class PriorCase(StrictModel):
    review_date: str
    category: str
    priority_signal: str
    team_conclusion: str | None = None
    human_decision: str | None = None
    human_reason: str | None = None
    same_camera: bool


class CaseHistory(StrictModel):
    category: str
    prior_cases: list[PriorCase]
    reviews_scanned: int
    notice: str = 'Earlier reviews at this site only. Recurrence context, not evidence of this event. Human decisions outrank model conclusions.'


@audited
def get_case_history(ctx:RunContext[AgentDeps],category:Category,limit:Annotated[int,Field(ge=1,le=5)]=5)->CaseHistory:
    """Look up up to five earlier findings of the same category at this site: team conclusion and any human decision (accepted, dismissed, needs evidence). Use it to judge recurrence or whether similar concerns were previously confirmed or dismissed. Never cite it as visual evidence."""
    d=ctx.deps;db=d.media.db;current=d.budget_workflow_id or d.workflow_id
    try:now=db.get('review_run',current)
    except KeyError:now={}
    cutoff=now.get('created_at') or '9999'
    camera=now.get('camera_id')
    cases=[c for c in db.list('video_safety_case') if c['site_id']==d.scope.site_id and c['category']==category
           and c['analysis_id']!=current and (c.get('created_at') or '')<cutoff]
    cases.sort(key=lambda c:c.get('created_at') or '',reverse=True)
    decisions={}
    for row in db.list('decision'):
        if row.get('target_id') and (row['target_id'] not in decisions or row.get('created_at','')>decisions[row['target_id']].get('created_at','')):decisions[row['target_id']]=row
    rows=[]
    for c in cases[:limit]:
        try:assessment=db.get('video_safety_assessment',c['id'])
        except KeyError:assessment=None
        conclusion=next((f.get('conclusion') for f in (assessment or {}).get('findings',[]) if f.get('conclusion')),None)
        decision=decisions.get(c['id'])
        rows.append(PriorCase(review_date=(c.get('created_at') or '')[:10],category=c['category'],priority_signal='see team conclusion' if conclusion else 'screening only',
            team_conclusion=(conclusion or '')[:240] or None,human_decision=decision.get('decision') if decision else None,
            human_reason=(decision.get('reason') or '')[:200] if decision else None,same_camera=bool(camera) and c.get('camera_id')==camera))
    return CaseHistory(category=category,prior_cases=rows,reviews_scanned=len({c['analysis_id'] for c in cases}))


get_case_history.parallel_safe=True  # read-only lookup; may run alongside other lookups in one turn

class PersonMove(StrictModel):
    from_frame: str
    to_frame: str
    from_box: list[float]
    to_box: list[float]
    dx: float = Field(description='Center shift in image widths (+ right).')
    dy: float = Field(description='Center shift in image heights (+ down).')
    scale_change: float = Field(description='Box area ratio; >1 can mean nearer the camera.')
    region_overlap: float | None = None


class PersonMotion(StrictModel):
    frames: list[dict]
    moves: list[PersonMove]
    unmatched_appearances: int
    unmatched_disappearances: int
    notes: list[str] = Field(default_factory=list)
    notice: str = 'Nearest-box matching between saved detections: not identity tracking, not physical distance or speed. Verify against pixels.'


def _iou(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    ix=max(0,min(ax+aw,bx+bw)-max(ax,bx));iy=max(0,min(ay+ah,by+bh)-max(ay,by))
    inter=ix*iy;union=aw*ah+bw*bh-inter
    return inter/union if union>0 else 0


def _distance(a,b):
    return ((a[0]+a[2]/2-b[0]-b[2]/2)**2+(a[1]+a[3]/2-b[1]-b[3]/2)**2)**.5


def _overlap(box,region):
    if not region:return None
    x,y,w,h=box;rx,ry,rw,rh=region
    ix=max(0,min(x+w,rx+rw)-max(x,rx));iy=max(0,min(y+h,ry+rh)-max(y,ry))
    return round(ix*iy/(w*h),3) if w*h>0 else 0


@audited
def compare_person_positions(ctx:RunContext[AgentDeps],frame_ids:list[str],region:list[float]|None=None)->PersonMotion:
    """Compare saved person boxes across 2-6 assigned frames in time order: who moved where, grew/shrank, appeared or left, and how much each box overlaps an optional normalized XYWH hazard region (e.g. a load path or edge). Deterministic matching only; useful for struck-by, fall and vehicle questions. Not identity, distance or speed. Problems are reported in the result, not raised."""
    d=ctx.deps
    if d.person_detections is None:raise ValueError('Person detections are unavailable for this stage')
    notes=[]
    ids=[]
    for f in frame_ids[:6]:
        try:fid=resolve_frame(d,f)
        except ValueError:fid=None
        if fid and fid in d.seen_frames and fid not in ids:ids.append(fid)
        else:notes.append(f'Skipped {f}: not an assigned frame.')
    if region is not None and (len(region)!=4 or min(region)<0 or region[0]+region[2]>1 or region[1]+region[3]>1 or region[2]<=0 or region[3]<=0):
        notes.append('Region ignored: use normalized [x, y, width, height] inside the image.');region=None
    if len(ids)<2:
        return PersonMotion(frames=[],moves=[],unmatched_appearances=0,unmatched_disappearances=0,notes=notes+['Need at least two assigned frames to compare.'])
    results=sorted((d.person_detections(f) for f in dict.fromkeys(ids)),key=lambda r:r.timestamp_ms)
    frames=[{'frame_id':r.frame_id,'timestamp_ms':r.timestamp_ms,'status':r.status,'people':len(r.people) if r.status=='available' else None,
             'region_overlaps':[_overlap(p.box,region) for p in r.people] if region and r.status=='available' else None} for r in results]
    moves=[];appear=disappear=0
    for a,b in zip(results,results[1:]):
        if a.status!='available' or b.status!='available':continue
        free=list(range(len(b.people)))
        for p in a.people:
            best=min(free,key=lambda j:_distance(p.box,b.people[j].box),default=None)
            # Nearest box center, gated by box size: overlap or a short move counts as the same detection.
            if best is None or (_iou(p.box,b.people[best].box)<.1 and _distance(p.box,b.people[best].box)>max(.15,1.5*max(p.box[2],p.box[3]))):disappear+=1;continue
            free.remove(best);q=b.people[best]
            (x,y,w,h),(x2,y2,w2,h2)=p.box,q.box
            moves.append(PersonMove(from_frame=a.frame_id,to_frame=b.frame_id,from_box=list(p.box),to_box=list(q.box),
                dx=round(x2+w2/2-x-w/2,3),dy=round(y2+h2/2-y-h/2,3),scale_change=round((w2*h2)/(w*h),2) if w*h>0 else 0,region_overlap=_overlap(q.box,region)))
        appear+=len(free)
    for r in results:d.interval(r.media_id,r.timestamp_ms,r.timestamp_ms)
    return PersonMotion(frames=frames,moves=moves[:24],unmatched_appearances=appear,unmatched_disappearances=disappear,notes=notes)

compare_person_positions.parallel_safe=True  # read-only lookup; may run alongside other lookups in one turn
