"""Parallel specialists, the run-level team combiner and the review-team tools. Offline only."""
import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
from safety_app.agents.safety_reviewer.models import SafetyRequest,SpecialistAssessment,SpecialistTask
from safety_app.domain.models import Observation
from safety_app.workflows.safety_team import SafetyTeam


class Audit:
    def __init__(self):self.rows=[]
    def write(self,event,**fields):self.rows.append((event,fields))


async def test_technical_specialist_waits_for_parallel_applicability(monkeypatch):
    order=[]
    async def fake_run_stage(rt,job,name,request,model_factory,previous,setup=None,parent_run_id=None,text_only=False,audit_provenance=None):
        order.append(('start',name));await asyncio.sleep(.05 if name.endswith('applicability') else .01);order.append(('end',name))
        deps=SimpleNamespace(run_id=name,agent_name=name,seen_sources=set(),seen_rules=set(),fetched_source_ids=set(),complete_source_ids=set())
        return SpecialistAssessment(specialist=name.removeprefix('specialist_')),deps
    import safety_app.workflows.agent_stage as stage
    monkeypatch.setattr(stage,'run_stage',fake_run_stage)
    obs=Observation(id='o',media_id='m',start_ms=0,end_ms=1,description='Fixture',evidence_refs=['frame'],method='human')
    team=SafetyTeam(SimpleNamespace(config={'review_team':{}},audit=Audit()),SimpleNamespace(payload={},run_id='r',id='j',kind='video_safety_review'),SafetyRequest(observations=[obs]),None,None)
    boss=SimpleNamespace(cancelled=lambda:False,run_id='boss',seen_sources=set(),seen_rules=set(),fetched_source_ids=set(),complete_source_ids=set())
    tech=SpecialistTask(question_id='q2',specialist='vehicles',question='Check the truck exposure.',observation_ids=['o'])
    scope=SpecialistTask(question_id='q1',specialist='applicability',question='Which applicability facts are known?',observation_ids=['o'])
    # The technical call arrives first in the same turn and must wait instead of failing.
    results=await asyncio.gather(team.delegate(tech,boss),team.delegate(scope,boss))
    assert [r.specialist for r in results]==['vehicles','applicability']
    assert order.index(('start','specialist_vehicles'))>order.index(('end','specialist_applicability'))
    two=SpecialistTask(question_id='q3',specialist='surfaces',question='Check the floor around the dock.',observation_ids=['o'])
    three=SpecialistTask(question_id='q4',specialist='machinery',question='Check the press guarding nearby.',observation_ids=['o'])
    order.clear();await asyncio.gather(team.delegate(two,boss),team.delegate(three,boss))
    # With applicability done, technical specialists overlap.
    assert order[:2]==[('start','specialist_surfaces'),('start','specialist_machinery')]


async def test_technical_specialist_alone_still_requires_applicability():
    obs=Observation(id='o',media_id='m',start_ms=0,end_ms=1,description='Fixture',evidence_refs=['frame'],method='human')
    team=SafetyTeam(SimpleNamespace(config={'review_team':{}},audit=Audit()),SimpleNamespace(payload={}),SafetyRequest(observations=[obs]),None,None)
    tech=SpecialistTask(question_id='q',specialist='vehicles',question='Check the truck exposure.',observation_ids=['o'])
    with pytest.raises(ValueError,match='applicability'):await team.delegate(tech,SimpleNamespace(cancelled=lambda:False))


def test_only_delegation_and_read_only_lookups_run_in_parallel():
    from safety_app.agents.safety_reviewer.tools import delegate_specialist,search_safety_sources,fetch_sources,get_site_rules,read_source
    from safety_app.agents.shared.people import get_person_detections
    from safety_app.agents.shared.case_tools import get_case_history,compare_person_positions
    from safety_app.agents.video_observer.tools import get_evidence
    for tool in (delegate_specialist,get_site_rules,read_source,get_person_detections,get_case_history,compare_person_positions):
        assert getattr(tool,'parallel_safe',False),tool.__name__
    for tool in (search_safety_sources,fetch_sources,get_evidence):  # these write evidence/source scope
        assert not getattr(tool,'parallel_safe',False),tool.__name__


def _ctx(frames,people,db=None,run='run-now'):
    from safety_app.domain.people import PersonFrame
    def lookup(fid):
        i=frames.index(fid)
        return PersonFrame(id='p'+fid,review_run_id=run,frame_id=fid,media_id='m',timestamp_ms=i*500,content_hash='h',status='available',
                           people=[{'id':f'{fid}-{k}','box':b,'confidence':.9} for k,b in enumerate(people[i])])
    deps=SimpleNamespace(seen_frames=set(frames),frame_alias_targets={},person_detections=lookup,interval=lambda *a:None,
        media=SimpleNamespace(db=db),scope=SimpleNamespace(site_id='local'),budget_workflow_id=run,workflow_id=run,
        cancelled=lambda:False,audit=Audit(),budgets=SimpleNamespace(tool=lambda run_id:None),run_id='agent',job_id='job',agent_name='safety_reviewer')
    return SimpleNamespace(deps=deps)


def test_compare_person_positions_tracks_overlap_without_identity():
    from safety_app.agents.shared.case_tools import compare_person_positions
    ctx=_ctx(['f1','f2','f3'],[[[.1,.5,.1,.3]],[[.2,.5,.12,.32]],[[.3,.5,.1,.3],[.8,.1,.05,.1]]])
    out=compare_person_positions(ctx,['f3','f1','f2'],region=[.25,.4,.2,.5])
    assert [f['frame_id'] for f in out.frames]==['f1','f2','f3']
    assert len(out.moves)==2 and out.moves[0].dx==pytest.approx(.11) and out.moves[1].region_overlap==1.0
    assert out.unmatched_appearances==1 and 'not identity' in out.notice
    soft=compare_person_positions(ctx,['f1','outside'],region=[.9,.9,.5,.5])
    assert not soft.moves and any('not an assigned frame' in n for n in soft.notes) and any('Region ignored' in n for n in soft.notes)


def test_case_history_is_site_scoped_earlier_only_and_keeps_human_decisions(tmp_path):
    from safety_app.infrastructure.repositories import Database
    from safety_app.agents.shared.case_tools import get_case_history
    db=Database(tmp_path/'app.sqlite')
    db.save('review_run',{'id':'run-now','created_at':'2026-10-25T10:00:00Z','camera_id':'cam'})
    base={'parent_job_id':'p','media_id':'m','source_hash':'h','episode_key':'e','mode':'hosted','escalation_enabled':True,'start_ms':0,'end_ms':1}
    db.save('video_safety_case',base|{'id':'old','analysis_id':'run-old','site_id':'local','camera_id':'cam','category':'struck_by','created_at':'2026-10-20T10:00:00Z'})
    db.save('video_safety_case',base|{'id':'future','analysis_id':'run-later','site_id':'local','camera_id':'cam','category':'struck_by','created_at':'2026-10-26T10:00:00Z'})
    db.save('video_safety_case',base|{'id':'other-site','analysis_id':'run-x','site_id':'elsewhere','camera_id':'cam','category':'struck_by','created_at':'2026-10-19T10:00:00Z'})
    db.save('video_safety_case',base|{'id':'current','analysis_id':'run-now','site_id':'local','camera_id':'cam','category':'struck_by','created_at':'2026-10-25T09:00:00Z'})
    db.save('video_safety_assessment',{'id':'old','findings':[{'conclusion':'Load swung over the walkway.'}]})
    db.save('decision',{'id':'d1','target_id':'old','decision':'accepted','reason':'Confirmed on site.','created_at':'2026-10-21T10:00:00Z'})
    out=get_case_history(_ctx(['f'],[[]],db),'struck_by')
    assert [c.team_conclusion for c in out.prior_cases]==['Load swung over the walkway.']
    assert out.prior_cases[0].human_decision=='accepted' and out.prior_cases[0].same_camera
    assert 'not evidence' in out.notice


def test_challenger_gets_one_crop_only_while_the_image_cap_has_room(monkeypatch):
    from safety_app.agents.challenge_reviewer import agent
    seen=[]
    monkeypatch.setattr(agent,'build',lambda name,model,deps,out,prompt,tools,**k:seen.append([t.__name__ for t in tools]) or deps)
    deps=SimpleNamespace(workflow_kind='video_safety_review',seen_frames={'a','b'},budgets=SimpleNamespace(config={'limits':{'frames':6}}),detail_frame_ids=set(),detail_limit=0)
    agent.create(None,deps);assert 'inspect_safety_detail' in seen[-1] and deps.detail_limit==1
    full=SimpleNamespace(workflow_kind='video_safety_review',seen_frames={str(i) for i in range(6)},budgets=SimpleNamespace(config={'limits':{'frames':6}}),detail_frame_ids=set(),detail_limit=0)
    agent.create(None,full);assert 'inspect_safety_detail' not in seen[-1]
    other=SimpleNamespace(workflow_kind='event_review',seen_frames={'a'},budgets=SimpleNamespace(config={'limits':{'frames':6}}),detail_frame_ids=set(),detail_limit=0)
    agent.create(None,other);assert 'inspect_safety_detail' not in seen[-1]


async def test_team_combiner_runs_once_after_case_reviews_and_feeds_the_report(tmp_path):
    import cv2,numpy as np
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.modules.safety_review.review_runs import ReviewRuns
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.workers.runner import execute
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path);folder=tmp_path/'data/input';folder.mkdir(parents=True)
    writer=cv2.VideoWriter(str(folder/'clip.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
    for i in range(120):writer.write(np.full((32,32,3),i%255,np.uint8))
    writer.release()
    body=VideoAnalysisRequest(source_id=sources(rt.root)[0].id,question='Review visible conditions',review_strategy='full',mode='fixture',idempotency_key='combiner')
    job=VideoAnalysisService(rt).request(body,'local');assert (await execute(rt,job)).status=='completed'
    status=ReviewRuns(rt).status(job.run_id,'local')
    for case in status.safety.cases:
        await execute(rt,case.review_job)
    with rt.db.connect() as db:rows=db.execute("SELECT body FROM jobs WHERE json_extract(body,'$.kind')='team_summary'").fetchall()
    assert len(rows)==1
    from safety_app.domain.models import Job
    summary=Job.model_validate_json(rows[0]['body'])
    assert summary.payload['review_run_id']==job.run_id
    import safety_app.workflows.agent_stage as stage
    real,caps=stage.run_stage,[]
    async def spy(rt,*a,**k):caps.append(rt.config['review_team']['max_workflow_requests']);return await real(rt,*a,**k)
    stage.run_stage=spy
    try:assert (await execute(rt,summary)).status=='completed'
    finally:stage.run_stage=real
    # The kitchen fire run used 91 requests: the combiner must use the run's frozen cap, not the global 64.
    assert caps==[256]
    brief=rt.db.get('team_brief',job.run_id)
    assert brief['priority_case_ids'] and set(brief['priority_case_ids'])<={c.case.id for c in status.safety.cases}
    data=report_data(rt,job.run_id,'local');html=report_html(data)
    assert data['team_brief']['summary'] in html and data['executive']['attention'][0]['case_id']==brief['priority_case_ids'][0]
    from safety_app.workers.runner import start_summaries
    start_summaries(rt)  # idempotent: a finished run never gets a second combiner job
    with rt.db.connect() as db:assert db.execute("SELECT count(*) FROM jobs WHERE json_extract(body,'$.kind')='team_summary'").fetchone()[0]==1


def test_historical_runs_without_the_flag_never_start_a_combiner(tmp_path):
    from safety_app.workflows.team_combiner import ready
    from safety_app.infrastructure.repositories import Database
    db=Database(tmp_path/'app.sqlite')
    db.save('review_run',{'id':'old','state':'ended','configuration':{'review_team':{}},'job_id':'j'})
    db.save('video_safety_case',{'id':'c','analysis_id':'old'})
    assert not ready(SimpleNamespace(db=db),'old')


def test_combiner_requests_a_priceable_cache_lifetime():
    """A paid check found combiner cache writes unpriceable without an explicit lifetime."""
    from safety_app.workflows.agent_stage import CACHE_LIFETIME_KINDS
    from safety_app.agents.shared.factory import UNIFORM_TTL_KINDS
    assert 'team_summary' in CACHE_LIFETIME_KINDS and set(CACHE_LIFETIME_KINDS)<=set(UNIFORM_TTL_KINDS)


def test_unknown_source_id_is_a_retrieval_gap_not_a_crash():
    from safety_app.agents.safety_reviewer.tools import read_source
    class Sources:
        def read(self,source_id,section,site_id):raise KeyError(source_id)
    deps=SimpleNamespace(scope=SimpleNamespace(source_ids=set(),site_id='local'),sources=Sources(),seen_sources=set(),source_bytes=6000,
        cancelled=lambda:False,audit=Audit(),budgets=SimpleNamespace(tool=lambda run_id:None),run_id='a',job_id='j',agent_name='challenge_reviewer')
    out=read_source(SimpleNamespace(deps=deps),'cfr-1910-22')
    assert out.available is False and not deps.seen_sources


def test_episode_is_closed_once_the_next_window_is_published(tmp_path):
    from safety_app.infrastructure.repositories import Database
    from safety_app.modules.safety_review.video_cases import episode_closed
    db=Database(tmp_path/'app.sqlite');rt=SimpleNamespace(db=db)
    db.save('review_run',{'id':'run','state':'running'})
    for i,st in enumerate(['completed','running','queued']):
        db.save('review_window',{'id':f'w{i}','run_id':'run','index':i,'start_ms':i*10000,'end_ms':(i+1)*10000,'status':st})
    case=lambda end:SimpleNamespace(end_ms=end)
    assert not episode_closed(rt,'run',case(4000))   # window 1 may still extend it
    db.save('review_window',{'id':'w1','run_id':'run','index':1,'start_ms':10000,'end_ms':20000,'status':'completed'})
    assert episode_closed(rt,'run',case(4000))       # the adjacent window is published
    assert not episode_closed(rt,'run',case(15000))  # still open until window 2 settles
    db.save('review_run',{'id':'run','state':'ended'})
    assert episode_closed(rt,'run',case(15000))


def test_live_claims_up_to_two_sealed_windows_per_run(tmp_path):
    from safety_app.infrastructure.runtime import Runtime
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    jobs=[]
    for i in range(3):
        j=rt.jobs.start('live_screening',f'w{i}',{'review_run_id':'run','window_id':f'win{i}'})
        rt.db.save('review_window',{'id':f'win{i}','run_id':'run','index':i,'start_ms':i*10000,'end_ms':(i+1)*10000,'job_id':j.id,'status':'queued'})
        jobs.append(j)
    assert rt.jobs.claim(['live_screening'],1).id==jobs[0].id
    assert rt.jobs.claim(['live_screening'],1) is None
    assert rt.jobs.claim(['live_screening'],2).id==jobs[1].id
    assert rt.jobs.claim(['live_screening'],2) is None


async def test_live_window_waits_for_earlier_windows_before_publishing(tmp_path):
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.workflows.full_review import wait_for_earlier_windows
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    job=rt.jobs.start('live_screening','w',{})
    for i,st in enumerate(['completed','running']):
        rt.db.save('review_window',{'id':f'v{i}','run_id':'run','index':i,'start_ms':i*10000,'end_ms':(i+1)*10000,'status':st})
    later=SimpleNamespace(id='v2',index=2);run=SimpleNamespace(id='run')
    import safety_app.workflows.full_review as fr
    class Windows:
        def __init__(self,rt):pass
        def windows(self,run_id):
            return [SimpleNamespace(**w) for w in rt.db.list('review_window') if w['run_id']==run_id]
    original=fr.ReviewRuns;fr.ReviewRuns=Windows
    try:
        task=asyncio.create_task(wait_for_earlier_windows(rt,run,later,job,limit_s=5))
        await asyncio.sleep(.5);assert not task.done()
        rt.db.save('review_window',{'id':'v1','run_id':'run','index':1,'start_ms':10000,'end_ms':20000,'status':'completed'})
        await asyncio.wait_for(task,3)
    finally:fr.ReviewRuns=original


async def test_live_wait_treats_terminal_jobs_as_settled_and_keeps_its_lease(tmp_path):
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.workflows.full_review import wait_for_earlier_windows
    import safety_app.workflows.full_review as fr
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    dead=rt.jobs.start('live_screening','dead',{});rt.jobs.finish(dead,'budget_exceeded')
    me=rt.jobs.start('live_screening','me',{})
    # The earlier window record still says running (its job died before updating it).
    rt.db.save('review_window',{'id':'v0','run_id':'run','index':0,'start_ms':0,'end_ms':10000,'status':'running','job_id':dead.id})
    beats=[];real=rt.jobs.heartbeat;rt.jobs.heartbeat=lambda id:(beats.append(id),real(id))
    class Windows:
        def __init__(self,rt):pass
        def windows(self,run_id):return [SimpleNamespace(**{'job_id':None,**w}) for w in rt.db.list('review_window') if w['run_id']==run_id]
    original=fr.ReviewRuns;fr.ReviewRuns=Windows
    try:await asyncio.wait_for(wait_for_earlier_windows(rt,SimpleNamespace(id='run'),SimpleNamespace(id='v1',index=1),me,limit_s=5),2)
    finally:fr.ReviewRuns=original
    # A live earlier window keeps this job's lease alive while it waits.
    other=rt.jobs.start('live_screening','other',{})
    rt.db.save('review_window',{'id':'v0','run_id':'run','index':0,'start_ms':0,'end_ms':10000,'status':'running','job_id':other.id})
    fr.ReviewRuns=Windows
    try:
        task=asyncio.create_task(wait_for_earlier_windows(rt,SimpleNamespace(id='run'),SimpleNamespace(id='v1',index=1),me,limit_s=5))
        await asyncio.sleep(1.2);assert me.id in beats and not task.done();task.cancel()
    finally:fr.ReviewRuns=original


def test_screening_observer_is_not_pushed_toward_extra_tool_round_trips():
    from pathlib import Path as P
    text=(P(__file__).resolve().parents[1]/'src/safety_app/agents/video_observer/video_observation_prompt.md').read_text()
    assert 'call compare_person_positions once' not in text and 'Call a tool only when its answer could change a check' in text


def test_tools_hide_when_the_next_round_would_exhaust_the_input_allowance(tmp_path):
    """Kitchen fire Live window 6: three tool rounds resent six frames each and the fourth request was blocked."""
    from safety_app.agents.shared.factory import input_gate
    from safety_app.infrastructure.repositories import Database
    db=Database(tmp_path/'app.sqlite')
    with db.connect() as c:c.execute('CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,run_id TEXT,workflow_id TEXT,day TEXT,month TEXT,amount INTEGER,actual INTEGER,state TEXT,input_tokens INTEGER,output_tokens INTEGER,created TEXT)')
    ctx=SimpleNamespace(deps=SimpleNamespace(run_id='obs',budgets=SimpleNamespace(db=db,config={'limits':{'input_tokens':32000}})))
    tool=object()
    assert input_gate(ctx,tool) is tool  # nothing sent yet
    def sent(i,tokens):
        with db.connect() as c:c.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?,NULL,'settled',?,?,?)",(str(i),'obs','run','d','m',1,tokens,10,'t'))
    sent(1,6000);assert input_gate(ctx,tool) is tool  # 26k left: a tool round and an answer still fit
    sent(2,9291);assert input_gate(ctx,tool) is None  # 16.7k left but growth is 3.3k per round: answer now
    with db.connect() as c:c.execute('DELETE FROM reservations')
    # Kitchen fire re-run, window 0: after a 9.6k first turn, another round would leave the answer 165 tokens short.
    sent(3,9600);assert input_gate(ctx,tool) is None


def test_combiner_input_separates_unfinished_reviews_from_left_out_moments(monkeypatch):
    """Kitchen fire: one fire case with 11 moments was briefed as "one team review remains pending"."""
    import safety_app.workflows.team_combiner as tc
    case=lambda cid,assessed,left:SimpleNamespace(case=SimpleNamespace(id=cid,category='fire_burn',start_ms=0),priority='high',status='assessed',
        assessment=SimpleNamespace(findings=[]) if assessed else None,unassessed_screening_ids=left,screenings=[])
    status=SimpleNamespace(decisions=[],run=SimpleNamespace(kind='live'),completed_windows=8,total_windows=8,coverage_complete=True,
        pending_specialists=1,safety=SimpleNamespace(cases=[case('fire',True,['s5','s6']),case('other',True,[])],screenings=[]))
    monkeypatch.setattr(tc,'ReviewRuns',lambda rt:SimpleNamespace(status=lambda run_id,site_id:status))
    coverage=tc.brief_input(None,'run','local')['coverage']
    assert coverage['pending_team_reviews']==0 and coverage['moments_not_in_team_packets']==2
    prompt=(Path(tc.__file__).parents[1]/'agents/safety_reviewer/combiner_prompt.md').read_text()
    assert 'Do not restate counts' in prompt


def test_report_lists_left_out_moments_instead_of_an_unfinished_review():
    from safety_app.modules.safety_review.run_report import executive
    screening=lambda sid,ms:{'id':sid,'check':{'start_ms':ms,'end_ms':ms+1000,'evidence_refs':[],'condition':'Flame near the cook.','result':'suspected'}}
    fire={'case':{'id':'fire','category':'fire_burn','start_ms':10000,'end_ms':78000},'priority':'high','status':'assessed',
          'assessment':{'findings':[]},'screenings':[screening('a',10000),screening('b',52000)],'unassessed_screening_ids':['b']}
    data={'safety':{'cases':[fire],'screenings':[]},'decisions':[],'run':{'state':'ended','kind':'live','start_ms':0,'end_ms':78000},'coverage_complete':True,
          'pending_specialists':1,'completed_windows':1,'total_windows':1,'windows':[{'status':'completed','start_ms':0,'end_ms':10000}],'summary':{}}
    steps=' '.join(executive(data)['actions'])
    assert 'still unfinished' not in steps and 'Watch 0:52 yourself: the team did not review this moment.' in steps
