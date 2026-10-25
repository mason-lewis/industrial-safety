import numpy as np
import json
import httpx
import pytest
from types import SimpleNamespace
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI
from app.agents.reviewers import ReviewDeps, build_agents, initial_packet
from app.agents.settings import ModelConfig, SharedBudget, BudgetExceeded
from app.evidence.store import EvidenceStore, EvidenceScope
from app.models.contracts import FramePacket, RuleCandidate, LabConfig, IncidentMarker
from app.storage.repository import Repository

CTX = dict(source_id="clip", camera_id="cam", session_id="s1", run_generation=0, schema_version="1.0")


def deps(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    ref = store.add(FramePacket(**CTX, frame_id=1, timestamp_ms=100, width=16, height=16, evidence_cutoff_ms=100),
                    np.zeros((16,16,3), dtype=np.uint8))
    candidate = RuleCandidate(**CTX, id="e1", kind="zone_dwell", timestamp_ms=100, start_ms=0, evidence_cutoff_ms=100)
    return ReviewDeps(store=store, repository=Repository(tmp_path / "records.db"), config=LabConfig(),
                      scope=EvidenceScope("s1",0,100), candidate=candidate), ref


def assessment(ref):
    return dict(**CTX, candidate_id="e1", evidence_cutoff_ms=100, review_mode="live",
                assessment="uncertain", severity="unknown", risk_type="zone_dwell",
                explanation="Synthetic plumbing review", observed_facts=["Fixture frame supplied"],
                evidence_refs=[ref.model_dump(mode="json")])


@pytest.mark.asyncio
async def test_real_image_bytes_reach_mocked_agent_with_typed_cited_output(tmp_path):
    d, ref = deps(tmp_path)
    packet = initial_packet(d)
    images = [part for part in packet if isinstance(part, BinaryContent)]
    assert len(images) == 1
    assert images[0].data.startswith(b"\xff\xd8")
    agents = build_agents(ModelConfig(), d, lambda name: TestModel(call_tools=[], custom_output_args=assessment(ref)))
    result = await agents["safety_coordinator"].run(["Review synthetic fixture", *packet], deps=d, usage=d.usage, usage_limits=d.budget.limits())
    assert result.output.candidate_id == "e1"
    assert result.output.evidence_refs == [ref]
    assert d.usage.requests == 1
    assert d.budget.requests == 1


@pytest.mark.asyncio
async def test_output_future_or_altered_evidence_is_rejected_with_bounded_retry(tmp_path):
    d, ref = deps(tmp_path)
    packet = initial_packet(d)
    bad = assessment(ref)
    bad["evidence_refs"][0]["timestamp_ms"] = 101
    agents = build_agents(ModelConfig(), d, lambda name: TestModel(call_tools=[], custom_output_args=bad))
    with pytest.raises(UnexpectedModelBehavior):
        await agents["safety_coordinator"].run(["Review",*packet], deps=d, usage=d.usage, usage_limits=d.budget.limits())
    assert d.budget.requests == 2


def test_model_cannot_cite_unretrieved_or_altered_ref(tmp_path):
    d, ref = deps(tmp_path)
    with pytest.raises(ValueError, match="not retrieved"):
        d.validate_refs([ref])
    initial_packet(d)
    with pytest.raises(ValueError, match="altered"):
        d.validate_refs([ref.model_copy(update={"frame_id":2})])


@pytest.mark.asyncio
async def test_request_cap_stops_before_model_request(tmp_path):
    d, ref = deps(tmp_path)
    packet = initial_packet(d)
    d.budget.request_limit = 1
    d.budget.requests = 1
    agents = build_agents(ModelConfig(), d, lambda name: TestModel(call_tools=[], custom_output_args=assessment(ref)))
    with pytest.raises(BudgetExceeded):
        await agents["safety_coordinator"].run(["Review",*packet], deps=d, usage=d.usage)
    assert d.usage.requests == 0


@pytest.mark.asyncio
async def test_actual_delegate_tree_shares_budget_and_usage(tmp_path,monkeypatch):
    monkeypatch.setenv("AI_SPECIALISTS_ENABLED","true")
    d, ref = deps(tmp_path)
    packet = initial_packet(d)
    d.budget.delegation_limit = 1
    def coordinator(messages, info):
        return ModelResponse(parts=[ToolCallPart("delegate_visual_review", {"question":"Check fixture visibility", "evidence_ids":[ref.id]})])
    def factory(name):
        if name == "safety_coordinator":
            return FunctionModel(coordinator)
        return TestModel(call_tools=[], custom_output_args={"observations":[], "evidence_refs":[]})
    agents = build_agents(ModelConfig(), d, factory)
    with pytest.raises(BudgetExceeded, match="delegation"):
        await agents["safety_coordinator"].run(["Review",*packet], deps=d, usage=d.usage, usage_limits=d.budget.limits())
    assert d.budget.delegations == 1
    assert d.budget.requests == 3  # Coordinator, child, coordinator; second child is blocked.
    assert d.usage.requests == 3
    assert d.budget.images == 2  # Initial image and one authorized delegated copy.


@pytest.mark.asyncio
async def test_response_intervals_remain_unknown_without_supported_anchors(tmp_path):
    d, ref = deps(tmp_path)
    d.candidate = None
    d.incident = IncidentMarker(**CTX, id="i1", timestamp_ms=100, pre_ms=100, post_ms=0)
    d.scope = EvidenceScope("s1",0,100,mode="retrospective")
    output = dict(**CTX, incident_id="i1", timeline=[{"label":"first visible response", "timestamp_ms":None,
                                                     "explanation":"Not established by evidence"}],
                  uncertainty=["Response action unavailable"])
    agents = build_agents(ModelConfig(), d, lambda name: TestModel(call_tools=[], custom_output_args=output))
    analyst = agents["incident_response_analyst"]
    tool = analyst._function_toolset.tools["calculate_intervals"].function
    assert await tool(SimpleNamespace(deps=d), [(None,100),(0,None),(100,0),(0,100)]) == [None,None,None,100]
    with pytest.raises(ValueError, match="outside"):
        await tool(SimpleNamespace(deps=d), [(0,101)])
    result = await analyst.run(["Describe only visible response",*initial_packet(d)], deps=d, usage=d.usage)
    assert result.output.timeline[0].timestamp_ms is None


@pytest.mark.asyncio
async def test_repeated_request_images_share_transmission_ceiling(tmp_path):
    d, ref = deps(tmp_path)
    d.budget.image_limit = 1
    packet = initial_packet(d)
    bad = assessment(ref)
    bad["candidate_id"] = "wrong"
    agents = build_agents(ModelConfig(), d, lambda name:TestModel(call_tools=[], custom_output_args=bad))
    with pytest.raises(BudgetExceeded,match="transmitted-image"):
        await agents["safety_coordinator"].run(["Review",*packet],deps=d,usage=d.usage)
    assert d.budget.transmitted_images == 1
    assert d.usage.requests == 1


@pytest.mark.asyncio
async def test_responses_http_payload_contains_jpeg_and_omits_reasoning_override(tmp_path,monkeypatch):
    """Actual SDK serialization with an entirely in-memory HTTP transport."""
    from pydantic_ai import models
    monkeypatch.setattr(models,"ALLOW_MODEL_REQUESTS",True)
    d,ref=deps(tmp_path)
    captured=[]
    def handler(request):
        assert request.url.host == "mock.invalid"
        body=json.loads(request.content)
        captured.append(body)
        final=next(tool["name"] for tool in body["tools"] if tool["name"].startswith("final_result"))
        return httpx.Response(200,json={"id":"resp_fixture","object":"response","created_at":0,
            "status":"completed","model":"gpt-6-luna","output":[{"type":"function_call","id":"fc_fixture",
                "call_id":"call_fixture","name":final,"arguments":json.dumps(assessment(ref)),"status":"completed"}],
            "usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client=AsyncOpenAI(api_key="test-not-real",base_url="https://mock.invalid/v1",http_client=http)
        provider=OpenAIProvider(openai_client=client)
        config=ModelConfig()
        agents=build_agents(config,d,lambda name:OpenAIResponsesModel(config.resolve(name),provider=provider))
        result=await agents["safety_coordinator"].run(["Fixture",*initial_packet(d)],deps=d,usage=d.usage)
    assert result.output.candidate_id=="e1"
    assert len(captured)==1
    assert captured[0]["model"]=="gpt-6-luna"
    assert "effort" not in captured[0].get("reasoning", {})
    assert "reasoning_effort" not in captured[0]
    images=[part for message in captured[0]["input"] if message.get("role")=="user"
            for part in message["content"] if part["type"]=="input_image"]
    assert len(images)==1
    assert images[0]["image_url"].startswith("data:image/jpeg;base64,/9j/")

@pytest.mark.asyncio
async def test_default_six_frame_delegate_finishes_synthesis(tmp_path,monkeypatch):
    monkeypatch.setenv("AI_SPECIALISTS_ENABLED","true")
    d,first=deps(tmp_path)
    for i in range(2,7):
        d.store.add(FramePacket(**CTX,frame_id=i,timestamp_ms=i*100,width=16,height=16,evidence_cutoff_ms=i*100),np.zeros((16,16,3),dtype=np.uint8))
    d.scope=EvidenceScope('s1',0,600);d.candidate.evidence_cutoff_ms=600
    packet=initial_packet(d);calls=0
    def coordinator(messages,info):
        nonlocal calls
        calls+=1
        if calls==1:
            return ModelResponse(parts=[ToolCallPart('delegate_visual_review',{'question':'Check all six frames','evidence_ids':list(d.seen_refs)})])
        output=assessment(first);output['evidence_cutoff_ms']=600
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,output)])
    def factory(name):
        return FunctionModel(coordinator) if name=='safety_coordinator' else TestModel(call_tools=[],custom_output_args={'observations':[],'evidence_refs':[]})
    agents=build_agents(ModelConfig(),d,factory)
    result=await agents['safety_coordinator'].run(['Review',*packet],deps=d,usage=d.usage,usage_limits=d.budget.limits())
    assert result.output.candidate_id=='e1'
    assert d.usage.requests==3 and d.budget.transmitted_images==18

@pytest.mark.asyncio
@pytest.mark.parametrize('cited',[False,True])
async def test_response_observations_require_per_claim_citations(tmp_path,cited):
    d,ref=deps(tmp_path);d.candidate=None
    d.incident=IncidentMarker(**CTX,id='i',timestamp_ms=100,post_ms=0)
    d.scope=EvidenceScope('s1',0,100,mode='retrospective')
    claim='Visible worker in the supplied frame'
    output=dict(**CTX,incident_id='i',observations=[claim],timeline=[])
    if cited:output['observation_evidence']={claim:[ref.model_dump(mode='json')]}
    agents=build_agents(ModelConfig(),d,lambda name:TestModel(call_tools=[],custom_output_args=output))
    if cited:
        result=await agents['incident_response_analyst'].run(['Review',*initial_packet(d)],deps=d,usage=d.usage)
        assert result.output.observation_evidence[claim]==[ref]
    else:
        with pytest.raises(UnexpectedModelBehavior):
            await agents['incident_response_analyst'].run(['Review',*initial_packet(d)],deps=d,usage=d.usage)

@pytest.mark.asyncio
async def test_event_history_excludes_future_closure(tmp_path):
    d,ref=deps(tmp_path)
    d.repository.save('candidate',d.candidate)
    d.repository.save('candidate',d.candidate.model_copy(update={'state':'closed','updated_ms':60000,'closed_ms':60000}))
    d.incident=IncidentMarker(**CTX,id='i',timestamp_ms=100,post_ms=0)
    agents=build_agents(ModelConfig(),d,lambda name:TestModel(call_tools=[]))
    tool=agents['incident_response_analyst']._function_toolset.tools['get_event_history'].function
    rows=await tool(SimpleNamespace(deps=d),0,100)
    assert rows[0]['state']=='candidate' and rows[0]['closed_ms'] is None
