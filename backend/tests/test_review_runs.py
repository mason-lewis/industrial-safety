import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from safety_app.infrastructure.runtime import Runtime
from safety_app.domain.video_analysis import VideoAnalysisRequest
from safety_app.modules.monitoring.recordings import sources
from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
from safety_app.modules.safety_review.review_runs import ReviewRuns, consecutive_windows
from safety_app.workers.runner import execute


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path)
    # Historical single-packet manifests must retain their original resume behavior.
    rt.config['video_screening']['temporal_sampling']='motion-context-1'
    return rt


def recording(rt,seconds=21):
    folder=rt.root/'data/input';folder.mkdir(parents=True)
    writer=cv2.VideoWriter(str(folder/'neutral.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(32,32))
    assert writer.isOpened()
    for i in range(seconds*10):writer.write(np.full((32,32,3),i%255,np.uint8))
    writer.release()
    return sources(rt.root)[0].id


def submit(rt,source):
    body=VideoAnalysisRequest(source_id=source,question='Review visible conditions',review_strategy='full',mode='fixture',idempotency_key='full')
    return VideoAnalysisService(rt).request(body,'local')


def test_consecutive_coverage_boundaries():
    assert consecutive_windows(0,1)==[(0,1)]
    assert consecutive_windows(100,20101)==[(100,10100),(10100,20100),(20100,20101)]
    windows=consecutive_windows(0,600000)
    assert len(windows)==60 and sum(b-a for a,b in windows)==600000
    assert all(windows[i][1]==windows[i+1][0] for i in range(59))


def test_explicit_review_cap_only_reduces_original_allowance(rt):
    source=recording(rt,1)
    body=VideoAnalysisRequest(source_id=source,question='Review visible conditions',review_strategy='full',
        mode='fixture',idempotency_key='reduced',max_cost_usd=.03)
    job=VideoAnalysisService(rt).request(body,'local')
    run=ReviewRuns(rt).get(job.run_id,'local')
    assert run.configuration['limits']['workflow_usd']==.03
    assert rt.config['limits']['workflow_usd']==.15
    assert run.configuration['limits']['day_usd']==2
    with pytest.raises(ValueError):body.model_copy().model_validate(body.model_dump()|{'max_cost_usd':.16})
    with pytest.raises(ValueError):body.model_copy().model_validate(body.model_dump()|{'review_strategy':'adaptive'})


async def test_full_review_checkpoint_report_and_all_case_handoffs(rt):
    job=submit(rt,recording(rt))
    assert job.review_run_id==job.run_id
    done=await execute(rt,job)
    assert done.status=='completed',done.error
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and status.completed_windows==3
    assert status.covered_ms==21000
    assert len(status.safety.screenings)==12
    assert status.pending_specialists==3 and not status.specialist_complete
    assert all(c.handoff for c in status.safety.cases)
    assert all(c.review_job.payload['jurisdiction']=='unknown' for c in status.safety.cases)
    assert status.usage['physical_requests']==0
    assert not status.resume_allowed
    for case in status.safety.cases:
        assessed=await execute(rt,case.review_job)
        assert assessed.status=='completed',assessed.error
    assert ReviewRuns(rt).status(job.run_id,'local').specialist_complete


async def test_cancel_resume_keeps_identity_and_completed_windows(rt):
    rt.config['review_team']['parallel_windows']=1  # call-count failure injection assumes window order
    job=submit(rt,recording(rt))
    from safety_app.workflows.video_fixture import models
    normal=models();calls=0
    def fail_after_first(name):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('Fixture interruption')
        return normal(name)
    failed=await execute(rt,job,fail_after_first)
    assert failed.status=='failed'
    service=ReviewRuns(rt)
    before=service.status(job.run_id,'local')
    assert before.completed_windows==1 and not before.coverage_complete
    chunk=rt.db.get('video_chunk',before.windows[0].id)
    service.resume(job.run_id,'local')
    resumed=await execute(rt,rt.jobs.get(job.id))
    assert resumed.status=='completed',resumed.error
    assert rt.db.get('video_chunk',before.windows[0].id)==chunk
    assert service.status(job.run_id,'local').coverage_complete
    assert len(rt.db.list('review_run'))==1


def test_source_change_and_uncertain_charge_block_resume(rt):
    job=submit(rt,recording(rt,1));service=ReviewRuns(rt)
    service.cancel(job.run_id,'local')
    ledger=rt.budgets
    reservation=ledger.reserve('paid-stage',job.run_id,'gpt-6-luna',1,1)
    ledger.reconcile(reservation)
    assert not service.status(job.run_id,'local').resume_allowed
    with pytest.raises(ValueError,match='uncertain'):service.resume(job.run_id,'local')


def test_cost_buckets_do_not_double_count_images_or_cache():
    from safety_app.infrastructure.usage_cost import token_cost
    price={'input_per_million':.1,'cached_input_per_million':.01,'output_per_million':.5,
           'cache_write_30m_per_million':.125,'cache_write_12h_per_million':.25,'image_usd':99,'version':'test'}
    usage=SimpleNamespace(input_tokens=1000,output_tokens=100,cache_read_tokens=200,cache_write_tokens=100,
        details={'cache_write_tokens_details':{'ttl_30m_tokens':100}},has_values=lambda:True)
    cost=token_cost(usage,price)
    assert cost['uncached_input_tokens']==700
    assert cost['usage_cost_usd']==pytest.approx((700*.1+200*.01+100*.125+100*.5)/1e6)
    usage.details={}
    assert token_cost(usage,price)['usage_cost_usd'] is None


@pytest.mark.parametrize('backlog',[1,30])
async def test_live_causal_windows_backpressure_eof_restart(rt,backlog):
    from app.pipeline import Pipeline
    from safety_app.modules.monitoring.live_review import LiveReview
    # 1 reproduces runs frozen before the backlog setting: one newest pending slot.
    rt.config['video_screening']['live_pending_windows']=backlog
    source=recording(rt,21)
    pipeline=Pipeline(rt.root)
    controller=LiveReview(rt,pipeline)
    body=VideoAnalysisRequest(source_id=source,question='Visible conditions',review_strategy='full',mode='fixture',idempotency_key='live')
    state=await controller.start(body,'local')
    run_id=state.run.id
    duplicate=await controller.start(body,'local')
    assert duplicate.run.id==run_id and duplicate.run.session_id==state.run.session_id
    assert not rt.jobs.cancelled(state.run.job_id)
    # Drive the real source decoder manually; the normal player loop remains paused.
    try:
        with pytest.raises(ValueError,match='1x'):await pipeline.playback('seek',timestamp_ms=15000)
        for _ in range(51):await pipeline.process_one()
        state=ReviewRuns(rt).status(run_id,'local')
        assert state.windows[0].status=='queued'
        first=rt.jobs.claim(['live_screening'])
        assert first and first.payload['window_id']==state.windows[0].id
        done=await execute(rt,first)
        assert done.status=='completed',done.error
        assert all(rt.db.get('frame',ref)['timestamp_ms']<=state.run.cutoff_ms for ref in state.windows[0].frame_ids)
        # Do not process the second window before EOF: analysis falls behind playback.
        while True:
            try:await pipeline.process_one()
            except StopAsyncIteration:break
        end=ReviewRuns(rt).status(run_id,'local')
        assert end.run.source_verified and end.run.state=='ended'
        if backlog==1:
            # The newest tail replaced the older pending slot, with an explicit gap.
            assert end.windows[1].status=='skipped' and end.windows[2].status=='queued'
            assert 'behind' in end.windows[1].gaps[0]
            tail=rt.jobs.claim(['live_screening']);assert tail
            assert (await execute(rt,tail)).status=='completed'
            assert not ReviewRuns(rt).status(run_id,'local').coverage_complete
        else:
            # The backlog keeps both waiting windows and drains them oldest-first after EOF.
            assert end.windows[1].status=='queued' and end.windows[2].status=='queued'
            for index in (1,2):
                job=rt.jobs.claim(['live_screening'])
                assert job and job.payload['window_id']==end.windows[index].id
                assert (await execute(rt,job)).status=='completed'
            assert ReviewRuns(rt).status(run_id,'local').coverage_complete
        restart=await controller.control(run_id,'restart','local')
        assert restart.run.id!=run_id and restart.run.generation==1
        assert ReviewRuns(rt).get(run_id,'local').source_hash==restart.run.source_hash
    finally:
        pipeline.task.cancel();await __import__('asyncio').gather(pipeline.task,return_exceptions=True)
        pipeline.source.close()


async def test_report_is_escaped_embedded_and_partial_is_explicit(rt):
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.domain.review_runs import RunDecisionInput
    job=submit(rt,recording(rt,1));await execute(rt,job)
    status=ReviewRuns(rt).status(job.run_id,'local')
    target=status.safety.cases[0].case.id
    decision=RunDecisionInput(target_id=target,decision='uncertain',reason='<script>alert(1)</script>')
    ReviewRuns(rt).decide(job.run_id,decision,SimpleNamespace(id='test-reviewer',site_id='local'))
    data=report_data(rt,job.run_id,'local');html=report_html(data)
    assert 'data:image/jpeg;base64,' in html
    assert '<script>alert' not in html and '&lt;script&gt;' in html
    assert 'Specialist assessment pending' in html
    assert 'Unassessable checks' in html and 'checks remain unknown' in html
    assert len(data['decisions'])==1
    with pytest.raises(ValueError,match='outside'):ReviewRuns(rt).status(job.run_id,'other-site')


def test_dotenv_root_then_parent_and_exported_empty_wins(tmp_path,monkeypatch):
    from safety_app.infrastructure.environment import load_environment
    root=tmp_path/'project';root.mkdir()
    (root/'.env').write_text('COURSE_TEST_A=root\nCOURSE_TEST_B=\nCOURSE_TEST_C=file\n')
    (tmp_path/'.env').write_text('COURSE_TEST_A=parent\nCOURSE_TEST_B=parent\nCOURSE_TEST_C=parent\n')
    monkeypatch.delenv('COURSE_TEST_A',raising=False);monkeypatch.delenv('COURSE_TEST_B',raising=False)
    monkeypatch.setenv('COURSE_TEST_C','')
    load_environment(root)
    import os
    assert os.environ['COURSE_TEST_A']=='root' and os.environ['COURSE_TEST_B']=='parent' and os.environ['COURSE_TEST_C']==''
    monkeypatch.delenv('COURSE_TEST_A');monkeypatch.delenv('COURSE_TEST_B')


async def test_ten_minute_recording_retains_all_sixty_windows_and_cases(rt):
    job=submit(rt,recording(rt,600))
    result=await execute(rt,job)
    assert result.status=='completed',result.error
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and status.completed_windows==60
    assert status.covered_ms==600000 and len(status.safety.cases)==60
    assert status.pending_specialists==60
    assert all(len(w.frame_ids)==6 for w in status.windows)
    assert status.usage['physical_requests']==0 and status.usage['input_tokens']==0
    from safety_app.modules.safety_review.run_report import report_data
    assert len(report_data(rt,job.run_id,'local')['safety']['cases'])==60


async def test_spatial_episodes_keep_separate_subjects_and_all_occurrences(rt):
    from safety_app.domain.models import MediaRef,Interval
    from safety_app.domain.video_analysis import VideoChunk
    from safety_app.modules.safety_review.video_cases import record_full_screenings
    job=submit(rt,recording(rt,21));await execute(rt,job)
    chunk=VideoChunk.model_validate(rt.db.list('video_chunk')[0])
    media=MediaRef.model_validate(rt.db.get('media',job.run_id))
    # Distinct boxes in the same window remain distinct; only the adjacent
    # matching box joins. Unknown localization never joins another subject.
    check=next(c for c in chunk.screening_checks if c.category=='blocked_access')
    body=VideoAnalysisRequest.model_validate(job.payload)
    for i,box in enumerate(([.1,.1,.2,.2],[.7,.1,.2,.2],[.1,.1,.2,.2],None)):
        lo=0 if i<2 else 10000
        signal=check.model_copy(update={'subject_box':box,'start_ms':lo,'end_ms':lo+10000})
        row=chunk.model_copy(update={'id':f'spatial-{i}','intervals':[Interval(start_ms=lo,end_ms=lo+10000)],'screening_checks':[signal]})
        record_full_screenings(rt,job,body,media,row)
        record_full_screenings(rt,job,body,media,row)  # crash replay is idempotent
    cases=ReviewRuns(rt).status(job.run_id,'local').safety.cases
    localized=[c for c in cases if c.case.subject_box]
    assert len(localized)==2
    assert sorted(len(c.screenings) for c in localized)==[1,2]
    assert max(c.case.end_ms for c in localized)==20000


async def test_scene_wide_fire_is_one_case_across_adjacent_intervals(rt):
    """Kitchen fire Live run: one spreading fire became 10 box-matched cases and exhausted the run cap."""
    from safety_app.domain.models import MediaRef,Interval
    from safety_app.domain.video_analysis import VideoChunk
    from safety_app.modules.safety_review.video_cases import record_full_screenings
    job=submit(rt,recording(rt,31));await execute(rt,job)
    chunk=VideoChunk.model_validate(rt.db.list('video_chunk')[0])
    media=MediaRef.model_validate(rt.db.get('media',job.run_id))
    body=VideoAnalysisRequest.model_validate(job.payload)
    before={c.case.id for c in ReviewRuns(rt).status(job.run_id,'local').safety.cases}
    base=chunk.screening_checks[0]
    fire=lambda lo,box:base.model_copy(update={'category':'fire_burn','result':'suspected','ppe_item':None,'subject_box':box,'start_ms':lo,'end_ms':lo+10000})
    # Two disjoint boxes in one interval, a moved box next interval, then a gap of one interval.
    plan=[(0,[[.1,.1,.2,.2],[.7,.6,.2,.2]]),(10000,[[.4,.3,.3,.3]]),(30000,[[.4,.3,.3,.3]])]
    for i,(lo,boxes) in enumerate(plan):
        row=chunk.model_copy(update={'id':f'fire-{i}','intervals':[Interval(start_ms=lo,end_ms=lo+10000)],'screening_checks':[fire(lo,b) for b in boxes]})
        record_full_screenings(rt,job,body,media,row);record_full_screenings(rt,job,body,media,row)
    fires=sorted((c for c in ReviewRuns(rt).status(job.run_id,'local').safety.cases if c.case.id not in before and c.case.category=='fire_burn'),key=lambda c:c.case.start_ms)
    assert [(c.case.start_ms,c.case.end_ms) for c in fires]==[(0,20000),(30000,40000)]
    assert [len(c.screenings) for c in fires]==[3,1]


async def test_live_future_and_stale_window_rejected_before_model(rt):
    from safety_app.domain.review_runs import ReviewWindow
    from safety_app.modules.safety_review.review_runs import update_record
    from safety_app.workflows.full_review import live_window
    job=submit(rt,recording(rt,21))
    update_record(rt,'review_run',job.run_id,kind='live',cutoff_ms=1000)
    window=ReviewRuns(rt).windows(job.run_id)[0]
    child=rt.jobs.start('live_screening','future',{'review_run_id':job.run_id,'window_id':window.id},'local')
    update_record(rt,'review_window',window.id,job_id=child.id)
    with pytest.raises(ValueError,match='future'):await live_window(rt,child)
    assert not rt.db.list('video_chunk')


def test_temporal_iou_one_to_one_and_scope():
    from app.evals.events import evaluate
    base={'source_id':'clip','camera_id':'camera','session_id':'test','event_type':'blocked_access','start_ms':0,'end_ms':10000}
    labels=[dict(base,id='truth')]
    predictions=[dict(base,id='first'),dict(base,id='duplicate'),dict(base,id='near',start_ms=6000,end_ms=16000),dict(base,id='foreign',source_id='other')]
    score=evaluate({'labels':labels,'predictions':predictions,'temporal_iou_threshold':.5})
    assert score['true_positives']==1 and score['precision']==.25 and score['recall']==1
    assert len(score['duplicate_ids'])==1


async def test_global_stop_keeps_completed_coverage_but_cancels_pending_outbox(rt,monkeypatch):
    from fastapi.testclient import TestClient
    from safety_app.api.main import app
    from safety_app.modules.safety_review.video_cases import dispatch_ready_cases
    # Screen with fixtures while outbox dispatch is held by the stop switch.
    job=submit(rt,recording(rt,2))
    rt.budgets.switch(True)
    done=await execute(rt,job)
    assert done.status=='completed'
    before=ReviewRuns(rt).status(job.run_id,'local')
    assert before.coverage_complete and before.pending_specialists>0
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    client=TestClient(app)
    assert client.post('/api/auth/session',json={}).status_code==200
    assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==200
    assert client.post('/api/budgets/stop',json={'stopped':False}).status_code==200
    assert dispatch_ready_cases(rt)==[]
    stopped=ReviewRuns(rt).status(job.run_id,'local')
    assert stopped.run.state=='cancelled' and stopped.coverage_complete
    assert stopped.pending_specialists==before.pending_specialists

async def test_pausing_stopped_history_run_is_idempotent_without_touching_other_camera(rt):
    from safety_app.modules.monitoring.live_review import LiveReview
    from safety_app.modules.safety_review.review_runs import update_record
    job=submit(rt,recording(rt,1));service=ReviewRuns(rt)
    service.prepare(job,VideoAnalysisRequest.model_validate(job.payload))
    update_record(rt,'review_run',job.run_id,kind='live',state='cancelled')
    rt.jobs.cancel(job.id)
    # No attached pipeline: pausing this historical run must be a read-only no-op.
    controller=LiveReview(rt,SimpleNamespace())
    assert (await controller.control(job.run_id,'pause','local')).run.state=='cancelled'
    with pytest.raises(ValueError):await controller.control(job.run_id,'play','local')
    with pytest.raises(ValueError):await controller.control(job.run_id,'pause','other-site')


async def test_report_matches_json_shows_team_and_labels_kind(rt,monkeypatch):
    import copy,re
    from safety_app.modules.safety_review.run_report import report_data,report_html
    from safety_app.api.routes import review_runs as routes
    from safety_app.api.auth import Operator
    job=submit(rt,recording(rt,12));await execute(rt,job)
    status=ReviewRuns(rt).status(job.run_id,'local')
    child=status.safety.cases[0].review_job
    assert (await execute(rt,child)).status=='completed'
    data=report_data(rt,job.run_id,'local');html=report_html(data)
    summary=data['summary']
    assert summary['findings']==len(data['safety']['cases']) and summary['completed_windows']==data['completed_windows']
    assert f'Findings <span class="n">{summary["findings"]}</span>' in html
    assert '<h1>Review report</h1>' in html and 'neutral.mp4' in html and data['source_name']=='neutral.mp4'
    roles={n['role'] for n in data['team']['nodes']}
    assert {'observer','lead','specialist','challenger'}<=roles
    assert 'Agent team' in html and 'Video observer' in html and 'Safety lead' in html
    assert data['ask']=={'turns':[]} and 'No questions were asked' in html
    assert 'DEMO' in html and re.search(r'\d:\d\d',html)
    assert not re.search(r'blocked access|slip_trip|unstable_stack',html)
    ex=data['executive']
    assert len(ex['groups'])==len({c['case']['category'] for c in data['safety']['cases']})  # one card per concern type
    assert sum(g['count'] for g in ex['groups'])==summary['findings'] and 'What needs attention' in html
    assert any(step.startswith('Decide on') for step in ex['actions']) and ex['context'].endswith('concern types') or ex['context'].endswith('concern type')
    framed=[f for f in data['frames'] if f.get('thumbnail')]
    assert framed and all(len(f['thumbnail'])<=len(f['embedded_image']) for f in framed)
    assert html.index('What needs attention')<html.index('Full details for auditors')<html.index('Agent team')
    # Live runs share the renderer but say so and explain skipped intervals.
    live=copy.deepcopy(data);live['run']['kind']='live';live['run']['state']='ended';live['run']['cutoff_ms']=8000
    live['windows'][-1]['status']='skipped';live['summary']['skipped_windows']=1
    page=report_html(live)
    assert '<h1>Live report</h1>' in page and 'Live session' in page and '1 interval(s) were skipped' in page
    monkeypatch.setattr(routes,'runtime',lambda:rt)
    response=routes.report(job.run_id,'html',user=Operator())
    assert 'attachment;' in response.headers['content-disposition']
    assert 'inline;' in routes.report(job.run_id,'html',inline=True,user=Operator()).headers['content-disposition']
    assert response.headers['cache-control']=='private, no-store'
    assert 'industrial-safety-review-' in response.headers['content-disposition']


async def test_parallel_window_screening_matches_sequential_publication(tmp_path):
    results=[]
    for width in (1,3):
        root=tmp_path/f'w{width}';root.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[2]/'configs',root/'configs')
        rt=Runtime(root);rt.config['review_team']['parallel_windows']=width
        job=submit(rt,recording(rt,35));assert (await execute(rt,job)).status=='completed'
        status=ReviewRuns(rt).status(job.run_id,'local')
        assert status.coverage_complete and [w.status for w in status.windows]==['completed']*4
        completed=[r['output']['window_id'] for r in sorted(rt.audit.rows(10000,event_type='review_window_completed'),key=lambda r:r['sequence'])]
        assert completed==[w.id for w in status.windows]  # published strictly in source-time order
        results.append(sorted((c.case.category,c.case.start_ms,c.case.end_ms) for c in status.safety.cases))
    assert results[0]==results[1]


async def test_parallel_window_failure_publishes_only_earlier_windows_and_resumes(rt):
    rt.config['review_team']['parallel_windows']=3
    job=submit(rt,recording(rt,35))
    from safety_app.workflows.video_fixture import models
    normal=models()
    started=[]
    def failing(name):
        started.append(name)
        if len(started)==2:raise RuntimeError('Fixture interruption in a parallel window')
        return normal(name)
    failed=await execute(rt,job,failing)
    assert failed.status=='failed'
    status=ReviewRuns(rt).status(job.run_id,'local')
    statuses=[w.status for w in status.windows]
    # Published windows form a prefix in source-time order; nothing after the failure is published.
    first_open=next(i for i,s in enumerate(statuses) if s!='completed')
    assert all(s!='completed' for s in statuses[first_open:])
    assert 'failed' in statuses and not status.coverage_complete
    ReviewRuns(rt).resume(job.run_id,'local')
    resumed=await execute(rt,rt.jobs.get(job.id))
    assert resumed.status=='completed',resumed.error
    assert ReviewRuns(rt).status(job.run_id,'local').coverage_complete


class APIConnectionError(Exception):pass


def unavailable():
    from safety_app.infrastructure.token_preflight import TokenPreflightUnavailable
    try:raise APIConnectionError('connection closed before response headers')
    except APIConnectionError as cause:
        try:raise TokenPreflightUnavailable('count unavailable') from cause
        except TokenPreflightUnavailable as exc:return exc


async def test_window_retries_once_when_token_count_fails_before_anything_is_sent(rt,monkeypatch):
    """Warehouse forklift Live run: three count attempts closed early and interval 0 went unchecked."""
    import safety_app.workflows.full_review as fr
    real=fr.run_stage;calls=[]
    async def flaky(*a,**k):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('agent run failed') from unavailable()
        return await real(*a,**k)
    monkeypatch.setattr(fr,'run_stage',flaky);monkeypatch.setattr(fr,'PREFLIGHT_RETRY_DELAY_S',0)
    job=submit(rt,recording(rt,11))
    assert (await execute(rt,job)).status=='completed'
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and len(calls)==len(status.windows)+1
    assert [r for r in rt.audit.rows(100) if r['event_type']=='packet_preflight_retry']


async def test_no_retry_after_a_request_was_reserved(rt,monkeypatch):
    import safety_app.workflows.full_review as fr
    calls=[]
    async def charged(*a,**k):
        calls.append(1)
        with rt.db.connect() as db:db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?,NULL,'settled',1,1,'t')",(str(len(calls)),'x',job.run_id,'d','m',1))
        raise unavailable()
    monkeypatch.setattr(fr,'run_stage',charged);monkeypatch.setattr(fr,'PREFLIGHT_RETRY_DELAY_S',0)
    job=submit(rt,recording(rt,9))
    assert (await execute(rt,job)).status=='failed' and len(calls)==1


async def test_only_one_retry_when_counting_stays_down(rt,monkeypatch):
    import safety_app.workflows.full_review as fr
    calls=[]
    async def down(*a,**k):calls.append(1);raise unavailable()
    monkeypatch.setattr(fr,'run_stage',down);monkeypatch.setattr(fr,'PREFLIGHT_RETRY_DELAY_S',0)
    job=submit(rt,recording(rt,9))
    assert (await execute(rt,job)).status=='failed' and len(calls)==2
