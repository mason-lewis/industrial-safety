"""Replay the six-image/tool-roundtrip regression through the real SDK, offline."""
import json
import shutil
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from openai import AsyncOpenAI
from pydantic_ai.providers.openai import OpenAIProvider

from safety_app.domain.video_analysis import VideoAnalysisRequest
from safety_app.infrastructure.budgets import BudgetBlocked
from safety_app.infrastructure.responses_model import UsageResponsesModel
from safety_app.infrastructure.runtime import Runtime
from safety_app.modules.monitoring.recordings import sources
from safety_app.modules.safety_review.review_runs import ReviewRuns
from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
from safety_app.workflows.full_review import full_review


@pytest.fixture
def review(tmp_path,request):
    shutil.copytree(Path(__file__).resolve().parents[2] / 'configs', tmp_path / 'configs')
    rt = Runtime(tmp_path)
    # Keep the original SDK/tool/count regression for historical manifests.
    rt.config['video_screening']['temporal_sampling']=getattr(request,'param','motion-context-1')
    # These regressions inject provider behavior by call order, so they pin one window at a time.
    rt.config['review_team']['parallel_windows']=1
    if rt.config['video_screening']['temporal_sampling']!='motion-sequence-3':
        rt.config['video_screening']['temporal_input_tokens']=24000
    folder = tmp_path / 'data/input'
    folder.mkdir(parents=True)
    writer = cv2.VideoWriter(str(folder / 'sample.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
    assert writer.isOpened()
    for i in range(143):
        writer.write(np.full((64, 64, 3), i % 255, np.uint8))
    writer.release()
    body = VideoAnalysisRequest(source_id=sources(rt.root)[0].id, question='Describe visible conditions',
                                review_strategy='full', mode='fixture', idempotency_key='count-regression')
    job = VideoAnalysisService(rt).request(body, 'local')
    return rt, job


class WireReplay:
    def __init__(self, failure=None, people_per_packet=1):
        self.requests = []
        self.failure = failure
        self.generations = 0
        self.people_per_packet = people_per_packet

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        assert body['model'] == 'gpt-6-luna'
        content = [part for item in body['input'] if item.get('role') == 'user'
                   for part in item['content']]
        assert sum(part['type'] == 'input_image' for part in content) == 6
        assert 'Coordinator screening checklist' in body['instructions']
        assert not any(tool['name'] == 'get_safety_checklist' for tool in body['tools'])
        # Tools are offered on each packet's first turn; the answer turn may hide them when another
        # frame-resending round would not fit the packet's input allowance.
        if self.generations % 2 == 0:
            assert any(tool['name'] == 'get_person_detections' for tool in body['tools'])
        assert any('screening_checks' in json.dumps(tool) for tool in body['tools'])
        count = 8318 if self.generations % 2 == 0 else 9000
        if request.url.path.endswith('/input_tokens'):
            if self.failure == 'count_unauthorized':
                return httpx.Response(401, json={'error': {'message': 'Fixture authorization unavailable'}})
            if self.failure == 'count_unavailable':
                return httpx.Response(503, json={'error': {'message': 'Fixture counting unavailable'}})
            return httpx.Response(200, json={'object': 'response.input_tokens',
                                           'input_tokens': 24001 if self.failure == 'too_large' else count})
        assert body['store'] is False
        assert body['prompt_cache_options'] == {'mode': 'implicit', 'ttl': '30m'}
        # Counting uses the same real SDK image, instruction and schema mapping.
        count_body = self.requests[-2][1]
        for key in ('model', 'input', 'instructions', 'tools', 'text', 'tool_choice', 'reasoning'):
            assert body.get(key) == count_body.get(key)
        self.generations += 1
        frames = [json.loads(part['text']) for part in content
                  if part['type'] == 'input_text' and '"evidence_id"' in part['text']]
        frame = frames[0]
        if self.generations % 2:
            tool = 'get_person_detections'
            arguments = {'frame_id': frame['evidence_id']}
            usage = {'input_tokens': 8318, 'output_tokens': 17, 'total_tokens': 8335,
                     'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 8192,
                                             'cache_write_tokens_details': {'ttl_30m_tokens': 8192, 'ttl_12h_tokens': 0}}}
        else:
            tool = next(t['name'] for t in body['tools'] if 'screening_checks' in json.dumps(t))
            arguments = {'summary': 'Offline replay; no factual footage assessment.', 'observations': [],
                         'screening_checks': [{'category': category, 'result': 'unknown', 'visibility': 'not_assessable',
                                               'media_id': frame['media_id'],
                                               'condition': 'Offline fixture only.', 'start_ms': frame['timestamp_ms'],
                                               'end_ms': frame['timestamp_ms'], 'evidence_refs': [frame['evidence_id']],
                                               **({'ppe_state': 'not_assessable'} if category == 'ppe' else {})}
                                              for category in ('ppe', 'blocked_access', 'unstable_stack', 'slip_trip')]}
            usage = {'input_tokens': 9000, 'output_tokens': 1100, 'total_tokens': 10100,
                     'input_tokens_details': {'cached_tokens': 8192, 'cache_write_tokens': 500,
                                             'cache_write_tokens_details': {'ttl_30m_tokens': 256, 'ttl_12h_tokens': 244}}}
        if self.failure == 'unknown_ttl':
            usage['input_tokens_details'].pop('cache_write_tokens_details')
        if self.failure == 'bad_ttl':
            usage['input_tokens_details']['cache_write_tokens_details'] = {'ttl_30m_tokens': 999999}
        if self.failure == 'missing_usage':
            usage = None
        output=[{'type': 'function_call', 'id': f'fc_{self.generations}',
                 'call_id': f'call_{self.generations}', 'name': tool, 'arguments': json.dumps(arguments)}]
        if self.generations % 2 and self.people_per_packet>1:
            output=[{'type':'function_call','id':f'fc_{self.generations}_{i}',
                'call_id':f'call_{self.generations}_{i}','name':tool,
                'arguments':json.dumps({'frame_id':f['evidence_id']})} for i,f in enumerate(frames[:self.people_per_packet])]
        return httpx.Response(200, json={
            'id': f'resp_fixture_{self.generations}', 'object': 'response', 'created_at': 1,
            'status': 'completed', 'model': 'gpt-6-luna', 'service_tier': 'default',
            'output': output,
            'usage': usage,
        })


async def run_wire(review, wire):
    rt, job = review
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        client = AsyncOpenAI(api_key='offline-fixture', base_url='https://mock.invalid/v1', http_client=http, max_retries=0)
        await full_review(rt, job, lambda _: UsageResponsesModel('gpt-6-luna', provider=OpenAIProvider(openai_client=client)))


async def test_six_images_real_tool_roundtrip_and_cache_split_settle_each_interval(review):
    rt, job = review
    wire = WireReplay()
    await run_wire(review, wire)
    assert wire.generations == 4
    assert len(wire.requests) == 8
    status = ReviewRuns(rt).status(job.run_id, 'local')
    assert status.completed_windows == 2 and status.coverage_complete
    assert all(len(window.frame_ids) == 6 for window in status.windows)
    with rt.db.connect() as db:
        rows = db.execute('SELECT * FROM reservations WHERE workflow_id=?', (job.run_id,)).fetchall()
    assert all(r['actual'] is not None and r['state'] == 'settled' for r in rows)
    assert sum(r['input_tokens'] for r in rows) == 34636
    # No per-image surcharge in actual cost; both reported TTL buckets survive.
    assert [r['actual'] for r in rows] == [1046, 756, 1046, 756]
    assert not rt.audit.rows(event_type='budget_blocked')
    responses = rt.audit.rows(event_type='model_response')
    assert {e['resources']['cache_write_12h_tokens'] for e in responses} == {0, 244}


async def test_each_of_six_retained_frames_can_get_one_local_person_lookup(review):
    rt,job=review;wire=WireReplay(people_per_packet=6)
    await run_wire(review,wire)
    assert wire.generations==4
    assert len(rt.audit.rows(event_type='tool_finished'))==12
    assert ReviewRuns(rt).status(job.run_id,'local').coverage_complete


@pytest.mark.parametrize('review',['motion-sequence-2','motion-sequence-3'],indirect=True)
async def test_two_temporal_packets_are_counted_and_checkpointed_separately(review):
    rt,job=review
    wire=WireReplay();stages=0
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire)) as http:
        client=AsyncOpenAI(api_key='offline-fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        def interrupted(_):
            nonlocal stages
            stages+=1
            if stages==2:raise RuntimeError('Local fixture interruption after first packet')
            return UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client))
        with pytest.raises(RuntimeError,match='after first packet'):
            await full_review(rt,job,interrupted)
        assert len(rt.db.list('review_packet'))==1 and not rt.db.list('video_chunk')
        assert wire.generations==2
        assert not ReviewRuns(rt).status(job.run_id,'local').coverage_complete
        await full_review(rt,job,lambda _:UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client)))
    assert wire.generations==8  # Finished packet is reused, not purchased again.
    status=ReviewRuns(rt).status(job.run_id,'local')
    assert status.coverage_complete and all(len(w.frame_ids)==12 for w in status.windows)
    assert len(rt.db.list('review_packet'))==4 and len(rt.db.list('video_chunk'))==2
    assert all(len(p['frames'])==6 for p in rt.db.list('review_packet'))
    assert status.usage['unknown_cost_requests']==0


@pytest.mark.parametrize('failure', ['count_unavailable', 'count_unauthorized', 'too_large'])
async def test_preflight_blocks_before_generation_and_leaves_no_reservation(review, failure):
    rt, job = review
    wire = WireReplay(failure)
    from safety_app.infrastructure.token_preflight import TokenPreflightUnavailable
    with pytest.raises(TokenPreflightUnavailable if failure.startswith('count_') else BudgetBlocked, match='not dispatched|not sent'):
        await run_wire(review, wire)
    # An unavailable count is retried once per packet (3 count attempts each); authorization is not.
    assert wire.generations == 0 and len(wire.requests) == (6 if failure=='count_unavailable' else 1)
    with rt.db.connect() as db:
        assert db.execute('SELECT count(*) FROM reservations').fetchone()[0] == 0


async def test_transient_tool_followup_count_recovers_without_repeating_generation(review):
    from safety_app.infrastructure.provider_transport import request_hook,response_hook
    class DisconnectedCount(WireReplay):
        failed=False
        ids=[]
        failed_payload=None
        def __call__(self,request):
            if request.url.path.endswith('/input_tokens'):
                self.ids.append(request.headers['X-Client-Request-Id'])
                if self.generations==1 and not self.failed:
                    self.failed=True
                    self.failed_payload=json.loads(request.content)
                    raise httpx.RemoteProtocolError('fixture disconnect',request=request)
                if self.generations==1:assert json.loads(request.content)==self.failed_payload
            return super().__call__(request)
    rt,job=review;wire=DisconnectedCount()
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire),event_hooks={'request':[request_hook],'response':[response_hook]}) as http:
        client=AsyncOpenAI(api_key='offline-fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        await full_review(rt,job,lambda _:UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client)))
    assert wire.failed and wire.generations==4
    assert len(wire.ids)==5 and len(set(wire.ids))==5
    assert ReviewRuns(rt).status(job.run_id,'local').coverage_complete
    failed=rt.audit.rows(event_type='token_preflight_failed')
    assert len(failed)==1 and failed[0]['resources']['read_only_retry_scheduled']
    assert failed[0]['resources']['generation_dispatched'] is False
    with rt.db.connect() as db:
        rows=db.execute('SELECT * FROM reservations').fetchall()
    assert len(rows)==4 and all(r['state']=='settled' for r in rows)


async def test_generation_rejected_by_provider_tls_is_resent_once_at_zero_charge(review):
    """A received bad_record_mac alert proves the request never arrived complete."""
    import ssl
    from safety_app.infrastructure.provider_transport import request_hook,response_hook
    class CorruptedUpload(WireReplay):
        corrupted=0
        def __call__(self,request):
            if not request.url.path.endswith('/input_tokens') and self.generations==2 and not self.corrupted:
                self.corrupted+=1
                trace=request.extensions['trace']
                async def emit():
                    for name in ('http11.send_request_headers.started','http11.send_request_body.complete','http11.receive_response_headers.failed'):
                        await trace(name,{})
                import asyncio
                asyncio.get_event_loop().create_task(emit())
                alert=ssl.SSLError(1,'fixture alert');alert.reason='SSLV3_ALERT_BAD_RECORD_MAC'
                error=httpx.ReadError('fixture corrupted upload',request=request);error.__cause__=alert
                raise error
            return super().__call__(request)
    rt,job=review;wire=CorruptedUpload()
    async with httpx.AsyncClient(transport=httpx.MockTransport(wire),event_hooks={'request':[request_hook],'response':[response_hook]}) as http:
        client=AsyncOpenAI(api_key='offline-fixture',base_url='https://mock.invalid/v1',http_client=http,max_retries=0)
        await full_review(rt,job,lambda _:UsageResponsesModel('gpt-6-luna',provider=OpenAIProvider(openai_client=client)))
    assert wire.corrupted==1 and wire.generations==4
    assert ReviewRuns(rt).status(job.run_id,'local').coverage_complete
    retry=rt.audit.rows(event_type='retry_scheduled')
    assert len(retry)==1 and retry[0]['resources']['unsent_basis']=='provider_rejected_corrupted_tls_record'
    with rt.db.connect() as db:rows=db.execute('SELECT * FROM reservations').fetchall()
    assert len(rows)==5 and all(r['state']=='settled' for r in rows)
    assert sum(r['actual']==0 for r in rows)==1
    assert ReviewRuns(rt).status(job.run_id,'local').usage['unknown_cost_requests']==0


@pytest.mark.parametrize('failure', ['missing_usage', 'bad_ttl'])
async def test_unpriced_terminal_usage_stops_before_tools_and_further_intervals(review, failure):
    rt, job = review
    wire = WireReplay(failure)
    with pytest.raises(BudgetBlocked, match='could not be priced'):
        await run_wire(review, wire)
    assert wire.generations == 1
    assert not rt.audit.rows(event_type='tool_started')
    with pytest.raises(BudgetBlocked, match='unresolved charge'):
        await run_wire(review, wire)
    assert wire.generations == 1 and len(wire.requests) == 2
    with rt.db.connect() as db:
        rows = db.execute('SELECT * FROM reservations').fetchall()
    assert len(rows) == 1 and rows[0]['actual'] is None and rows[0]['state'] == 'unknown'
    if failure == 'bad_ttl':assert rows[0]['input_tokens'] == 8318


async def test_total_only_cache_writes_use_the_explicit_request_lifetime(review):
    rt, job = review
    wire = WireReplay('unknown_ttl')
    await run_wire(review, wire)
    assert wire.generations == 4
    assert ReviewRuns(rt).status(job.run_id, 'local').coverage_complete
    responses = rt.audit.rows(event_type='model_response')
    assert all(e['resources']['cache_write_breakdown_basis'] == 'uniform_request_ttl:30m' for e in responses)
    assert not rt.budgets.status()['outstanding']


def test_provider_split_never_replaces_inconsistent_usage_details():
    from pydantic_ai.usage import RequestUsage
    from safety_app.infrastructure.usage_cost import token_cost
    usage = RequestUsage(input_tokens=100, output_tokens=1, cache_write_tokens=100,
                         details={'cache_write_tokens_details': {'ttl_30m_tokens': 1}})
    price = {'input_per_million': .1, 'output_per_million': .5, 'cache_write_30m_per_million': .125}
    assert token_cost(usage, price, {'cache_write_tokens_details': {'ttl_30m_tokens': 100}})['usage_cost_usd'] is None


def test_live_keeps_evidence_but_does_not_queue_more_paid_windows_after_unknown_charge(review):
    from safety_app.modules.monitoring.live_review import LiveReview
    rt, job = review
    service = ReviewRuns(rt)
    run = service.get(job.run_id, 'local').model_copy(update={'mode': 'hosted', 'kind': 'live'})
    controller = LiveReview(rt, None)
    controller.run = run
    reservation = rt.budgets.reserve('first-window', run.id, 'gpt-6-luna', 100, 100)
    rt.budgets.reconcile(reservation)
    for window in service.windows(run.id):
        controller.reset_samples()
        for i in range(6):
            controller.sampler.add(str(window.start_ms + i * 200), window.start_ms + i * 200, np.full((64, 64, 3), i * 20, np.uint8))
        controller.seal(window, window.end_ms)
    assert all(w.status == 'skipped' and w.frame_ids and 'unresolved charge' in w.gaps[0]
               for w in service.windows(run.id))
    assert not any(j.kind == 'live_screening' for j in rt.jobs.list())
    with rt.db.connect() as db:
        assert db.execute('SELECT count(*) FROM reservations').fetchone()[0] == 1
