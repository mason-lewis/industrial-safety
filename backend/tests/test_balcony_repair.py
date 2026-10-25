"""Offline connection, cumulative allowance and original-pixel regressions."""
import json
import shutil
from pathlib import Path
import cv2
import httpx
import numpy as np
import pytest
from openai import AsyncOpenAI
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.messages import ModelRequest,UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from safety_app.infrastructure.runtime import Runtime
from safety_app.infrastructure.budgets import BudgetBlocked,BudgetLedger
from safety_app.infrastructure.provider_transport import request_hook,response_hook,RequestTrace
from safety_app.infrastructure.responses_model import UsageResponsesModel
from safety_app.agents.shared.factory import GuardedModel
from safety_app.infrastructure.review_allowances import create_test_budget,register_detail,snapshot
from safety_app.infrastructure.frame_detail import original_crop
from safety_app.domain.models import MediaRef


@pytest.fixture
def rt(tmp_path):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    return Runtime(tmp_path)


def bad_record_alert(reason='SSLV3_ALERT_BAD_RECORD_MAC'):
    import ssl
    error=ssl.SSLError(1,'fixture TLS alert');error.reason=reason;error.library='SSL'
    return error


# (transport events, raised error, provably unsent?)
TRANSPORT_CASES={
    'connect_failed':(['connection.connect_tcp.failed'],lambda r:httpx.ConnectError('secret-token-must-not-be-exported',request=r),True),
    'tls_handshake_failed':(['connection.start_tls.failed'],lambda r:httpx.ConnectError('secret-token-must-not-be-exported',request=r),True),
    'body_write_failed':(['http11.send_request_headers.started','http11.send_request_headers.complete','http11.send_request_body.started','http11.send_request_body.failed','http11.response_closed.complete'],
                         lambda r:httpx.WriteError('secret-token-must-not-be-exported',request=r),True),
    'peer_bad_record_mac':(['http11.send_request_headers.started','http11.send_request_body.complete','http11.receive_response_headers.failed','http11.response_closed.complete'],
                           lambda r:_chained(httpx.ReadError('secret-token-must-not-be-exported',request=r),bad_record_alert()),True),
    'local_bad_record_mac':(['http11.send_request_headers.started','http11.send_request_body.complete','http11.receive_response_headers.failed'],
                            lambda r:_chained(httpx.ReadError('secret-token-must-not-be-exported',request=r),bad_record_alert('DECRYPTION_FAILED_OR_BAD_RECORD_MAC')),False),
    'response_read_failed':(['http11.send_request_headers.started','http11.send_request_body.complete','http11.receive_response_headers.failed','http11.response_closed.complete'],
                            lambda r:httpx.ReadError('secret-token-must-not-be-exported',request=r),False),
}


def _chained(error,cause):
    error.__cause__=cause
    return error


@pytest.mark.parametrize('case',TRANSPORT_CASES)
async def test_transport_proof_controls_zero_vs_unknown_and_retries_only_unsent(rt,case):
    events,failure,settled=TRANSPORT_CASES[case]
    calls=[]
    async def wire(request):
        calls.append(request)
        assert request.headers['X-Client-Request-Id']
        trace=request.extensions['trace']
        for name in events:await trace(name,{})
        raise failure(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire),event_hooks={'request':[request_hook],'response':[response_hook]}) as http:
        async with AsyncOpenAI(api_key='test',max_retries=0,http_client=http) as client:
            model=UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
            guard=GuardedModel(model,rt.budgets,rt.audit,'stage','workflow')
            with pytest.raises(RuntimeError,match='connection to OpenAI'):
                await guard.request([ModelRequest(parts=[UserPromptPart('offline fixture')])],{'max_tokens':80},ModelRequestParameters())
    # One bounded resend, with a new request ID, only when the first provably never arrived.
    assert len(calls)==(2 if settled else 1)
    assert len({c.headers['X-Client-Request-Id'] for c in calls})==len(calls)
    with rt.db.connect() as db:rows=db.execute('SELECT * FROM reservations').fetchall()
    assert len(rows)==len(calls)
    assert all(r['actual']==(0 if settled else None) for r in rows)
    assert all(r['state']==('settled' if settled else 'unknown') for r in rows)
    event=rt.audit.rows(1,event_type='model_response')[0]
    transport=event['resources']['transport']
    assert transport['proven_not_dispatched']==settled and bool(transport['unsent_basis'])==settled
    assert transport['failed_phase']==next((e for e in events if e.endswith('.failed')),None)
    retries=rt.audit.rows(event_type='retry_scheduled')
    assert len(retries)==(1 if settled else 0)
    if settled:assert retries[0]['resources']['unsent_basis']==transport['unsent_basis']
    assert 'secret-token' not in json.dumps(rt.audit.rows(event_type='model_response'))


def prior(rt):
    r=rt.budgets.reserve('old-stage','old-run','gpt-6-luna',10,10)
    rt.budgets.reconcile(r)
    return r


def test_visual_qualifications_are_preserved_without_turning_into_legal_support():
    from safety_app.agents.safety_reviewer.models import AgentFinding
    from safety_app.agents.safety_reviewer.finding_evidence import preserve_visual_gaps
    from safety_app.domain.models import ElementCheck
    finding=AgentFinding(review_id='review',condition='Possible board contact.',evidence_refs=['f1','f2'],classification='general_hazard',
        element_checks=[ElementCheck(element='Contact confirmed',status='unknown',explanation='Contact is not resolved in the supplied stills.')])
    assert preserve_visual_gaps(finding)
    assert finding.uncertainty[0]=='Contact is not resolved in the supplied stills.'
    assert 'applicability is not established' in finding.uncertainty[1]
    assert not preserve_visual_gaps(finding)
    legal=finding.model_copy(update={'classification':'supported_noncompliance','uncertainty':[]})
    assert not preserve_visual_gaps(legal) and not legal.uncertainty
    qualified=finding.model_copy(update={'uncertainty':['Existing qualification remains verbatim.']})
    assert not preserve_visual_gaps(qualified)
    assert qualified.uncertainty==['Existing qualification remains verbatim.']


def test_nested_specialist_references_use_only_exact_assigned_aliases():
    from types import SimpleNamespace
    from safety_app.agents.safety_reviewer.models import SpecialistAssessment,AgentFinding
    from safety_app.agents.shared.evidence_refs import normalize_video_references
    deps=SimpleNamespace(seen_frames={'review-5300000'},frame_alias_targets={'f1':{'review-5300000'},'5300000':{'review-5300000'}})
    result=SpecialistAssessment(specialist='vehicles',findings=[AgentFinding(review_id='review',condition='Possible contact',
        evidence_refs=['f1'],element_checks=[{'element':'Contact','status':'unknown','evidence_refs':['5300000'],'explanation':'Uncertain.'}])],
        hypotheses=[{'id':'motion','explanation':'Possible movement','status':'unresolved','supporting_refs':['f1','foreign-frame'],
            'missing_facts':['Contact not established'],'distinguishing_test':'Inspect adjacent pixels.'}])
    normalize_video_references(deps,result)
    assert result.findings[0].evidence_refs==['review-5300000']
    assert result.findings[0].element_checks[0].evidence_refs==['review-5300000']
    assert result.hypotheses[0].supporting_refs==['review-5300000','foreign-frame']
    from safety_app.workflows.safety_team import validate_hypotheses
    deps.seen_sources=set();deps.seen_rules=set()
    with pytest.raises(ValueError,match='unseen'):validate_hypotheses(result.hypotheses,deps)


async def test_cancel_after_failed_read_only_count_never_retries_or_reserves(rt):
    import asyncio
    from safety_app.infrastructure.token_preflight import count_input
    cancelled=False
    class Counter:
        model_name='gpt-6-luna'
        calls=0
        async def count_tokens(self,*args):
            nonlocal cancelled
            self.calls+=1;cancelled=True
            raise httpx.ConnectError('offline disconnect')
    model=Counter()
    with pytest.raises(asyncio.CancelledError):
        await count_input(model,[ModelRequest(parts=[UserPromptPart('fixture')])],{},ModelRequestParameters(),rt.budgets,rt.audit,'count',lambda:cancelled)
    assert model.calls==1
    with rt.db.connect() as db:
        assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==0


def test_cumulative_budget_survives_restart_and_only_named_unknown_is_allowed(rt):
    unknown=prior(rt)
    create_test_budget(rt.budgets,'funding','one-test',['old-run'],[unknown])
    job=rt.jobs.start('video_analysis','one-test',{})
    with pytest.raises(BudgetBlocked,match='connection check'):
        rt.budgets.reserve('new-stage',job.run_id,'gpt-6-luna',10,10)
    rt.db.save('review_connection_probe',{'id':job.run_id,'status':'reachable'})
    ledger=BudgetLedger(rt.db,rt.audit,rt.config)
    reservation=ledger.reserve('new-stage',job.run_id,'gpt-6-luna',10,10)
    ledger.reconcile(reservation)
    with pytest.raises(BudgetBlocked,match='new unresolved'):
        ledger.require_settled(job.run_id)
    with rt.db.connect() as db:
        assert db.execute('SELECT state FROM reservations WHERE id=?',(unknown,)).fetchone()[0]=='unknown'
    assert rt.jobs.start('video_analysis','one-test',{}).run_id==job.run_id
    assert snapshot(ledger,job.run_id)['new_run_cap_micro']==70000
    from safety_app.infrastructure.review_allowances import close_test
    rt.jobs.finish(job,'failed')
    close_test(ledger,job.run_id,'Single authorized attempt finished')
    assert snapshot(ledger,job.run_id)['closed']
    with pytest.raises(BudgetBlocked,match='single funded test'):
        BudgetLedger(rt.db,rt.audit,rt.config).require_settled(job.run_id)


def test_test_budget_rejects_unlisted_unknown_and_enforces_cost(rt):
    unknown=prior(rt)
    with pytest.raises(ValueError,match='exactly'):
        create_test_budget(rt.budgets,'bad','bad',['old-run'],[])
    create_test_budget(rt.budgets,'fund','new',['old-run'],[unknown],total_cap=.15,new_run_cap=.001)
    job=rt.jobs.start('video_analysis','new',{})
    rt.db.save('review_connection_probe',{'id':job.run_id})
    with pytest.raises(BudgetBlocked,match='Cumulative'):
        rt.budgets.reserve('new-stage',job.run_id,'gpt-6-luna',10000,1000)


def test_detail_limits_are_shared_by_observer_verifier_and_restart(rt):
    register_detail(rt.budgets,'pass1','run','observer1')
    register_detail(rt.budgets,'pass1','run','verify1','verifier')
    register_detail(rt.budgets,'pass2','run','observer2')
    with pytest.raises(BudgetBlocked,match='Two'):
        register_detail(rt.budgets,'pass3','run','observer3')
    with pytest.raises(BudgetBlocked,match='already attempted'):
        register_detail(rt.budgets,'pass1','run','replacement')
    r=rt.budgets.reserve('observer1','run','gpt-6-luna',10,10)
    rt.budgets.reconcile(r,.009)
    with pytest.raises(BudgetBlocked,match='Temporal detail'):
        rt.budgets.reserve('verify1','run','gpt-6-luna',10000,1000)


def test_crop_uses_original_pixels_and_preserves_parent_and_time(rt,monkeypatch):
    media=MediaRef(id='media',media_id='media',source_id='source',camera_id='camera',content_hash='sourcehash',storage_key='fixture',start_ms=0,end_ms=10000)
    rt.db.save('media',media)
    image=np.zeros((1080,1920,3),np.uint8)
    image[300:700,400:800]=[40,150,220]
    ok,jpeg=cv2.imencode('.jpg',image);assert ok
    frame=rt.media.add_frame('media','pts-159',5300,jpeg.tobytes())
    monkeypatch.setattr(rt.media,'model_image',lambda *_:pytest.fail('Crop must not read downscaled pixels'))
    meta,raw=original_crop(rt.media,frame.id,[.2,.2,.4,.5])
    assert meta['parent_hash']==frame.content_hash and meta['source_hash']=='sourcehash'
    assert meta['timestamp_ms']==5300 and meta['original_detail_pixels']==[768,540]
    decoded=cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
    assert decoded.shape[:2]==(540,768)


def test_temporal_selection_and_crop_are_generic():
    from types import SimpleNamespace
    from safety_app.workflows.temporal_detail import detail_frames,detail_box
    frames=[SimpleNamespace(timestamp_ms=t) for t in [5600,5200,5300,5100,5400,5500]]
    assert [f.timestamp_ms for f in detail_frames(frames)]==[5100,5300,5600]
    check=SimpleNamespace(person_box=[.2,.4,.1,.2],subject_box=[.2,.2,.2,.2])
    box=detail_box(check)
    assert box[1]<.2 and box[1]+box[3]>.6
    assert detail_frames(frames[:2])==[]


@pytest.mark.parametrize('cv_localization',[False,True])
async def test_focused_pass_has_six_images_checked_claim_and_immutable_parent(rt,monkeypatch,cv_localization):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.domain.review_runs import ReviewRun
    from safety_app.domain.video_analysis import VideoChunk
    from safety_app.domain.video_safety import VisualSafetyCheck
    from safety_app.workflows.temporal_detail import refine_packet,refine_assigned_case
    job=rt.jobs.start('video_analysis','detail-test',{'source_id':'source','question':'Describe the visible footage',
        'review_strategy':'full','mode':'fixture','idempotency_key':'detail-test','media_ids':[]})
    rt.db.save('media',MediaRef(id=job.run_id,media_id=job.run_id,source_id='source',camera_id='camera-1',
        content_hash='sourcehash',storage_key='fixture',start_ms=0,end_ms=10000))
    run=ReviewRun(id=job.run_id,job_id=job.id,site_id='local',source_id='source',source_hash='sourcehash',
        media_id=job.run_id,camera_id='camera-1',start_ms=0,end_ms=10000,kind='review',mode='fixture',
        configuration=rt.config,request=job.payload)
    rt.db.save('review_run',run)
    frames=[]
    for i in range(6):
        ok,jpeg=cv2.imencode('.jpg',np.full((720,1280,3),20+i*20,np.uint8));assert ok
        frames.append(rt.media.add_frame(job.run_id,str(i),5100+i*100,jpeg.tobytes()))
    original=VisualSafetyCheck(category='struck_by',result='suspected',visibility='partial',condition='Unverified original hypothesis',
        evidence_refs=[f.id for f in frames],start_ms=5100,end_ms=5600,subject_box=[.2,.2,.2,.4],temporal_basis='sequence_event')
    if cv_localization:
        from types import SimpleNamespace
        original.subject_box=None
        monkeypatch.setattr(rt.people,'get',lambda *args:SimpleNamespace(status='available',people=[SimpleNamespace(box=[.2,.2,.2,.4])]))
    packet=VideoChunk(id='packet',analysis_id=job.run_id,media_id=job.run_id,intervals=[{'start_ms':0,'end_ms':10000}],
        summary='Fixture',observations=[],frames=frames,limitations=[],observer_run_id='first',screening_checks=[original])
    rt.db.save('review_packet',packet,immutable=True)
    calls=[]
    def factory(name):
        def respond(messages,info):
            from pydantic_ai import BinaryContent
            content=[c for m in messages for p in m.parts if hasattr(p,'content') and isinstance(p.content,list) for c in p.content]
            assert sum(isinstance(c,BinaryContent) for c in content)==6
            calls.append(name)
            value=original.model_dump()|{'condition':'Fixture material descends with apparent contact; injury is unknown.',
                'evidence_refs':['f1','f2','f3']} if name=='video_observer' else {
                'checks':[{'claim_id':'acute','verdict':'supported','explanation':'Mocked pixel check; not real accuracy evidence.'}],
                'answer_supported':True}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,value)])
        return FunctionModel(respond)
    result=await refine_packet(rt,job,run,packet,factory)
    assert calls==['video_observer','challenge_reviewer']
    assert result.screening_checks[0].condition.startswith('Fixture material descends')
    assert rt.db.get('review_packet','packet')['screening_checks'][0]['condition']=='Unverified original hypothesis'
    assert len(rt.db.list('temporal_detail'))==1
    again=await refine_packet(rt,job,run,packet,factory)
    from types import SimpleNamespace
    assigned=await refine_assigned_case(rt,job,SimpleNamespace(category='struck_by',analysis_id=run.id),SimpleNamespace(check=original),factory)
    assert assigned.model_dump()==result.screening_checks[0].model_dump()
    assert again==result and len(calls)==2
    assert len(rt.db.list('review_refined_packet'))==1
    rt.db.save('review_run',run)
    from safety_app.api.routes import review_runs
    from safety_app.api.auth import Operator
    from unittest.mock import patch
    with patch.object(review_runs,'runtime',return_value=rt):
        page=review_runs.activity(run.id,0,Operator())
        assert any(e.event_type=='model_request' for e in page.events)
        assert not review_runs.activity(run.id,page.cursor,Operator()).events
        with pytest.raises(ValueError,match='outside site'):
            review_runs.activity(run.id,0,Operator(site_id='foreign'))
        detail=rt.db.list('temporal_detail')[0]
        response=review_runs.detail_crop(run.id,detail['id'],0,Operator())
        assert Path(response.path).is_file()
        with pytest.raises(ValueError,match='outside'):
            review_runs.detail_crop(run.id,detail['id'],3,Operator())
