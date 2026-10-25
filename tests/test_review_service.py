import asyncio
import numpy as np
import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.exceptions import ModelHTTPError
from app.agents.service import ReviewService, response_intervals
from app.evidence.store import EvidenceStore
from app.models.contracts import FramePacket, RuleCandidate, LabConfig, IncidentMarker, ResponseTimeline
from app.storage.repository import Repository

CTX = dict(source_id="clip", camera_id="cam", session_id="s1", run_generation=0, schema_version="1.0")


def setup(tmp_path, monkeypatch, factory=None):
    monkeypatch.setenv("AI_ENABLED", "false")
    monkeypatch.setenv("HOSTED_IMAGES_ALLOWED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-nonsecret-key")
    store = EvidenceStore(tmp_path / "evidence")
    ref = store.add(FramePacket(**CTX, frame_id=1, timestamp_ms=100, width=16, height=16, evidence_cutoff_ms=100), np.zeros((16,16,3),dtype=np.uint8))
    repo = Repository(tmp_path / "runs.sqlite")
    candidate = RuleCandidate(**CTX, id="e", kind="zone_dwell", timestamp_ms=100, start_ms=0, evidence_cutoff_ms=100)
    repo.save("candidate", candidate)
    generation = {"value":0}
    service = ReviewService(store, repo, lambda:LabConfig(analysis_mode='ai_cv'), lambda session,g:session=="s1" and g==generation["value"], factory)
    output = dict(**CTX, candidate_id="e", evidence_cutoff_ms=100, assessment="uncertain", severity="unknown",
                  risk_type="zone_dwell", explanation="Fixture review", observed_facts=["Supplied fixture"],
                  evidence_refs=[ref.model_dump(mode="json")])
    return service, candidate, repo, generation, output


async def drain(service):
    await asyncio.wait_for(service.queue.join(), 2)


@pytest.mark.asyncio
async def test_key_present_does_not_bypass_disabled_or_submit_backlog(tmp_path, monkeypatch):
    calls=[]
    def factory(name):
        calls.append(name)
        raise AssertionError("Disabled mode must never create a model")
    service, candidate, repo, generation, output = setup(tmp_path, monkeypatch, factory)
    await service.enqueue(candidate)
    assert candidate.ai_status == "disabled"
    assert service.state()["enabled"] is False
    with pytest.raises(ValueError, match="AI disabled"):
        await service.manual_review(candidate)
    incident = IncidentMarker(**CTX,id="i",timestamp_ms=100)
    with pytest.raises(ValueError, match="AI disabled"):
        await service.review_incident(incident)
    service.set_enabled(True,True)
    assert service.queue.empty()
    assert calls == []
    assert service.total_requests == 0
    await service.close()


@pytest.mark.asyncio
async def test_no_credentials_or_image_permission_preserves_candidate(tmp_path, monkeypatch):
    service, candidate, repo, generation, output = setup(tmp_path,monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    service.set_enabled(True,True)
    await service.enqueue(candidate)
    assert repo.get("candidate","e")["ai_status"] == "error"
    assert service.state()["available"] is False
    assert service.total_requests == 0
    service.set_enabled(True,False)
    with pytest.raises(ValueError,match="Hosted image"):
        await service.manual_review(candidate)
    await service.close()


@pytest.mark.asyncio
async def test_typed_review_preserves_each_assessment_and_rule_lifecycle(tmp_path,monkeypatch):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    service.model_factory=lambda name:TestModel(call_tools=[],custom_output_args=output)
    service.set_enabled(True,True)
    await service.enqueue(candidate)
    # Model processing is asynchronous and must preserve this rule state update.
    saved=candidate.model_copy(update={"state":"closed"})
    repo.save("candidate",saved)
    await drain(service)
    assert repo.get("candidate","e")["state"] == "closed"
    assert repo.get("candidate","e")["ai_status"] == "reviewed"
    await service.manual_review(candidate)
    await drain(service)
    assessments=repo.list("assessment")
    assert len(assessments)==2
    assert len({a["id"] for a in assessments})==2
    assert all(a["evidence_cutoff_ms"]==100 for a in assessments)
    assert repo.list("review_provenance")[0]["model_config"]["settings"]=={}
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["timeout","rate_limit","malformed"])
async def test_model_failure_preserves_cv_candidate_and_is_explicit(tmp_path,monkeypatch,failure):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    if failure=="timeout":
        monkeypatch.setenv("AI_LIVE_DEADLINE_S","0.01")
        async def fail(messages,info):
            await asyncio.sleep(1)
        model=FunctionModel(fail)
    elif failure=="rate_limit":
        def fail(messages,info):
            raise ModelHTTPError(429,"test-model",{"error":"rate limit"})
        model=FunctionModel(fail)
    else:
        model=TestModel(call_tools=[],custom_output_args={"assessment":"invented","candidate_id":"wrong"})
    service.model_factory=lambda name:model
    service.set_enabled(True,True)
    await service.enqueue(candidate)
    await drain(service)
    status="timeout" if failure=="timeout" else "error"
    assert repo.get("candidate","e")["ai_status"]==status
    assert repo.get("candidate","e")["state"]=="candidate"
    assert repo.list("assessment")==[]
    assert repo.list("agent_run")[-1]["status"]==status
    assert service.errors==1
    await service.close()


@pytest.mark.asyncio
async def test_cancel_and_late_generation_output_are_rejected(tmp_path,monkeypatch):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    started=asyncio.Event()
    release=asyncio.Event()
    async def delayed(messages,info):
        started.set()
        await release.wait()
        from pydantic_ai.messages import ModelResponse,ToolCallPart
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,output)])
    service.model_factory=lambda name:FunctionModel(delayed)
    service.set_enabled(True,True)
    await service.enqueue(candidate)
    await asyncio.wait_for(started.wait(),1)
    generation["value"]=1
    release.set()
    await drain(service)
    assert repo.list("assessment")==[]
    assert repo.list("agent_run")[-1]["status"]=="cancelled"
    with pytest.raises(ValueError,match="historical"):
        await service.manual_review(candidate)
    await service.close()


def test_response_intervals_are_unknown_until_both_supported_timestamps_exist():
    assert response_intervals([])=={"anchor_to_response":None,"anchor_to_control":None,"anchor_to_clearance":None}
    timeline=[ResponseTimeline(label="incident_anchor",timestamp_ms=100),ResponseTimeline(label="first_visible_response",timestamp_ms=250)]
    assert response_intervals(timeline)["anchor_to_response"]==150
    assert response_intervals(timeline)["anchor_to_control"] is None


@pytest.mark.asyncio
async def test_disable_cancels_active_review_and_retains_candidate(tmp_path,monkeypatch):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    started=asyncio.Event()
    async def hold(messages,info):
        started.set()
        await asyncio.sleep(1)
    service.model_factory=lambda name:FunctionModel(hold)
    service.set_enabled(True,True)
    await service.enqueue(candidate)
    await asyncio.wait_for(started.wait(),1)
    service.set_enabled(False,True)
    await drain(service)
    assert repo.list("assessment")==[]
    assert repo.get("candidate","e")["ai_status"]=="disabled"
    assert repo.list("agent_run")[-1]["status"]=="cancelled"
    assert service.pending==set()
    await service.close()


@pytest.mark.asyncio
async def test_full_queue_preserves_candidate_and_stale_packet_is_skipped(tmp_path,monkeypatch):
    monkeypatch.setenv("AI_QUEUE_LIMIT","1")
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    service.model_factory=lambda name:TestModel(call_tools=[],custom_output_args=output)
    service.set_enabled(True,True)
    blocker=asyncio.create_task(asyncio.sleep(1))
    service.worker=blocker
    await service.enqueue(candidate)
    extra=candidate.model_copy(update={"id":"other"})
    await service.enqueue(extra)
    assert repo.get("candidate","other")["ai_status"]=="skipped"
    assert service.queue.qsize()==1
    assert service.total_requests==0
    blocker.cancel()
    await asyncio.gather(blocker,return_exceptions=True)
    service.store.add(FramePacket(**CTX,frame_id=2,timestamp_ms=6100,width=16,height=16,evidence_cutoff_ms=6100),np.zeros((16,16,3),dtype=np.uint8))
    service.worker=asyncio.create_task(service._worker(service.queue,False))
    await drain(service)
    assert repo.get("candidate","e")["ai_status"]=="skipped"
    assert service.total_requests==0
    assert service.skipped==2
    await service.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('setting',['reasoning','budget'])
async def test_setup_failure_is_terminal_and_worker_handles_next_job(tmp_path,monkeypatch,setting):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    service.model_factory=lambda name:TestModel(call_tools=[],custom_output_args=output)
    service.set_enabled(True,True)
    if setting=='reasoning': service.model_config.reasoning_effort='invalid'
    else: monkeypatch.setenv('AI_LIVE_REQUEST_LIMIT','invalid')
    await service.enqueue(candidate);await drain(service)
    assert repo.get('candidate','e')['ai_status']=='error'
    assert repo.list('agent_run')[-1]['status']=='error'
    assert not service.pending
    service.model_config.reasoning_effort=None;monkeypatch.delenv('AI_LIVE_REQUEST_LIMIT',raising=False)
    await service.manual_review(candidate);await drain(service)
    assert repo.list('agent_run')[-1]['status']=='reviewed'
    await service.close()

@pytest.mark.asyncio
async def test_manual_review_uses_original_lifecycle_snapshot(tmp_path,monkeypatch):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    closed=candidate.model_copy(update={'state':'closed','updated_ms':60000,'closed_ms':60000})
    repo.save('candidate',closed)
    service.model_factory=lambda name:TestModel(call_tools=[],custom_output_args=output)
    service.set_enabled(True,True)
    await service.manual_review(closed);await drain(service)
    assert repo.candidate_at('e',100)['state']=='candidate'
    assert repo.candidate_at('e',60000)['state']=='closed'
    assert repo.get('candidate','e')['state']=='closed'
    await service.close()

@pytest.mark.asyncio
async def test_late_procedures_are_withheld_from_live_review(tmp_path,monkeypatch):
    service,candidate,repo,generation,output=setup(tmp_path,monkeypatch)
    candidate.processing_time='2026-10-03T12:00:00+00:00';repo.save('candidate',candidate)
    repo.save('procedures',{'id':'config-1','captured_at':'2026-10-03T13:00:00+00:00','documents':{'later.md':'Future procedure'}})
    service.model_factory=lambda name:TestModel(call_tools=[],custom_output_args=output)
    service.set_enabled(True,True)
    await service.manual_review(candidate);await drain(service)
    assert repo.list('review_provenance')[-1]['procedures_sha256']=={}
    await service.close()
