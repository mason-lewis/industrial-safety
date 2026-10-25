"""Offline adversarial fixtures for indexed retrieval and bounded safety delegation."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from safety_app.infrastructure.repositories import Database
from safety_app.modules.safety_review.sources import Sources
from safety_app.domain.models import SourceExcerpt,Finding,CausalHypothesis,Investigation
from safety_app.agents.safety_reviewer.models import SafetyResult,SpecialistTask,SpecialistAssessment
from safety_app.workflows.safety_team import SafetyTeam,validate_finding,validate_hypotheses
from safety_app.infrastructure.budgets import BudgetBlocked

@pytest.fixture
def indexed(tmp_path):return Sources(Database(tmp_path/'sources.sqlite'))

def store(service,id,text,**kwargs):
    value=SourceExcerpt(id=id,source_id='fixture',title='Powered industrial trucks',section='1910.178',text=text,version='fixture-v1',source_type='requirement',site_id='s',jurisdiction='US-federal-OSHA',**kwargs)
    service.db.save('source',value,immutable=True);return value

def test_index_ranking_exact_lookup_alias_scope_and_revision_cache(indexed):
    store(indexed,'a','Training requirements.')
    store(indexed,'z','No person shall stand under the elevated portion of the truck.',clause='1910.178(m)(2)')
    result=indexed.discover('stand elevated portion','US-federal-OSHA','',1,'s')
    assert result.candidates[0].id=='z' and len(result.candidates[0].preview)<=160
    assert not hasattr(result.candidates[0],'text')
    assert indexed.discover('1910.178(m)(2)','US-federal-OSHA','',1,'s').candidates[0].id=='z'
    assert indexed.discover('forklift','US-federal-OSHA','',1,'s').candidates
    assert indexed.discover('truck','US-federal-OSHA','',1,'foreign').candidates==[]
    assert indexed.discover('truck','US-federal-OSHA','',1,'s',{'a'}).candidates[0].id=='a'
    assert not indexed.discover('unique missing token','US-federal-OSHA','',1,'s').candidates
    store(indexed,'new','Unique missing token appears here.')
    assert indexed.discover('unique missing token','US-federal-OSHA','',1,'s').candidates[0].id=='new'
    indexed.db.list=lambda *args:(_ for _ in ()).throw(AssertionError('Runtime search must not scan all objects'))
    assert indexed.discover('stand elevated','US-federal-OSHA','',1,'s').candidates[0].id=='z'

def test_parent_context_no_neighbor_chain_no_truncation_and_date(indexed):
    store(indexed,'scope','Only covered industrial trucks.',effective_date='2020-01-01',context_complete=True)
    store(indexed,'related','Unless an exception applies.',effective_date='2020-01-01',context_complete=True,related_ids=['not-selected'])
    store(indexed,'clause','No person may pass beneath raised forks.',clause='1910.178(m)(2)',parent_ids=['scope'],related_ids=['related'],effective_date='2020-01-01',context_complete=True)
    packet=indexed.fetch_packet(['clause'],'s',max_bytes=2400,event_date='2026-10-05')
    assert packet.complete and {s.id for s in packet.excerpts}=={'scope','clause','related'}
    assert len(packet.model_dump_json().encode())<=2400
    limited=indexed.fetch_packet(['clause'],'s',{'clause'},max_bytes=2400,event_date='2026-10-05')
    assert not limited.complete and any('outside assigned scope' in x for x in limited.gaps)
    store(indexed,'big','MUST NEVER CUT THIS REQUIREMENT. '*500)
    big=indexed.fetch_packet(['big'],'s',max_bytes=700)
    assert not big.excerpts and not big.complete and len(big.model_dump_json().encode())<=700
    assert not indexed.fetch_packet(['clause'],'s',event_date='2019-01-01').excerpts
    store(indexed,'expired','Expired requirement.',effective_date='2020-01-01',effective_until='2023-01-01')
    assert not indexed.discover('expired','US-federal-OSHA','',1,'s',event_date='2026-10-05').candidates
    assert indexed.discover('expired','US-federal-OSHA','',1,'s',event_date='2022-01-01').candidates

def test_domain_filters_do_not_hide_unmapped_sources(indexed):
    store(indexed,'vehicle','Unique route issue.')
    indexed.db.save('source',SourceExcerpt(id='energy',source_id='e',title='Energy',section='1910.147',text='Unique route issue.',source_type='requirement',version='v',site_id='s',jurisdiction='US-federal-OSHA'))
    indexed.db.save('source',SourceExcerpt(id='unknown',source_id='u',title='Special site procedure',section='unmapped',text='Unique route issue.',source_type='procedure',version='v',site_id='s',jurisdiction='site'))
    ids={r.id for r in indexed.discover('unique route','US-federal-OSHA','',5,'s',specialist='vehicles').candidates}
    assert ids=={'vehicle','unknown'}
    assert {r.id for r in indexed.discover('unique route','US-federal-OSHA','',5,'s').candidates}=={'vehicle','unknown','energy'}

def deps(service):
    return SimpleNamespace(seen_frames={'frame'},seen_sources={'law'},seen_rules=set(),complete_source_ids=set(),sources=service,scope=SimpleNamespace(site_id='s'),event_date=None)

def test_rejects_regulation_as_cause_and_unsupported_legal_classification(indexed):
    store(indexed,'law','Mandatory rule.',clause='1910.178(m)(2)')
    d=deps(indexed)
    with pytest.raises(ValueError,match='regulation alone'):
        validate_hypotheses([CausalHypothesis(id='h',explanation='Training caused this incident.',status='supported',supporting_refs=['law'],distinguishing_test='Check training records against observed task.')],d)
    with pytest.raises(ValueError,match='unseen'):
        validate_hypotheses([CausalHypothesis(id='h',explanation='Visibility issue.',status='supported',supporting_refs=['foreign-frame'],distinguishing_test='Measure the actual sightline.')],d)
    finding=Finding(review_id='r',condition='Person beneath forks.',classification='supported_noncompliance',exact_clause='1910.178(m)(2)',source_refs=['law'],conclusion='Noncompliance',evidence_refs=['frame'])
    with pytest.raises(ValueError,match='unresolved elements'):validate_finding(finding,d)
    finding.classification='potential_violation';validate_finding(finding,d)
    finding.source_refs=[]
    with pytest.raises(ValueError,match='exact clause'):validate_finding(finding,d)

def test_library_clause_export_is_conservative_and_retains_context():
    from scripts.import_safety_library import records
    snapshot=Path(__file__).resolve().parents[1]/'data/resources/safety/snapshots/federal-2026-09-30'
    rows=records(snapshot,'fixture-site')
    clause=next(r for r in rows if r.clause=='1910.178(m)(2)')
    assert 'stand or pass under the elevated' in clause.text and clause.parent_ids
    assert clause.specialist=='vehicles' and not clause.context_complete and clause.effective_date is None
    assert any(r.clause=='29 USC 654(a)(1)' for r in rows)
    assert any(r.clause=='1910.134(f)' and 'Fit testing' in r.text for r in rows)

@pytest.mark.asyncio
async def test_question_ownership_invalid_followups_and_caps():
    from safety_app.agents.safety_reviewer.models import SafetyRequest
    from safety_app.domain.models import Observation
    rt=SimpleNamespace(config={'review_team':{'max_delegations':1}},audit=None)
    obs=Observation(id='o',media_id='m',start_ms=0,end_ms=1,description='Fixture',evidence_refs=['frame'],method='human')
    team=SafetyTeam(rt,SimpleNamespace(payload={}),SafetyRequest(observations=[obs]),None,None)
    boss=SimpleNamespace(cancelled=lambda:False)
    task=SpecialistTask(question_id='q',specialist='vehicles',question='Check the truck exposure.',observation_ids=['o'])
    with pytest.raises(ValueError,match='applicability'):await team.delegate(task,boss)
    team.results=[SpecialistAssessment(specialist='applicability',question_id='app',call_id='app-call')]
    team.owners['q']='energy'
    with pytest.raises(ValueError,match='owned'):await team.delegate(task,boss)
    team.owners.clear();task.phase='follow_up';task.previous_call_id='absent'
    with pytest.raises(ValueError,match='latest prior'):await team.delegate(task,boss)
    task.observation_ids=['foreign']
    with pytest.raises(ValueError,match='scope'):await team.delegate(task,boss)
    team.calls['existing']=task
    with pytest.raises(BudgetBlocked):await team.delegate(task,boss)

def test_existing_source_defaults_do_not_rewrite_historical_json(tmp_path):
    db=Database(tmp_path/'history.sqlite')
    old={'id':'old','source_id':'old','title':'Legacy procedure','section':'full','text':'Preserved original text.','source_type':'procedure','version':'v1','site_id':'s','jurisdiction':'site','retrieved_at':'2026-10-01','effective_date':None,'url':None}
    db.save('source',old,immutable=True)
    db.save('source',SourceExcerpt.model_validate(old),immutable=True)
    assert db.get('source','old')==old

def test_boss_rejects_fabricated_findings_duplicates_and_cause(indexed):
    from safety_app.agents.safety_reviewer.models import SafetyRequest,AgentFinding
    team=SafetyTeam(SimpleNamespace(config={}),SimpleNamespace(payload={}),SafetyRequest(observations=[]),None,None)
    finding=AgentFinding(id='f',review_id='r',condition='Visible fixture condition.',question_id='q',specialist='vehicles',classification='insufficient_evidence',conclusion='Unknown',evidence_refs=['frame'])
    team.results=[SpecialistAssessment(specialist='vehicles',question_id='q',findings=[finding])]
    d=deps(indexed)
    investigation=Investigation(limitations=['Fictional cause cannot be established.'])
    result=SafetyResult(findings=[finding],investigation=investigation)
    team.validate_boss(result,d)
    result.findings=[finding.model_copy(update={'condition':'Invented training deficiency'})]
    with pytest.raises(ValueError,match='invented'):team.validate_boss(result,d)
    result.findings=[finding,finding.model_copy(update={'id':'duplicate'})]
    with pytest.raises(ValueError,match='repeated'):team.validate_boss(result,d)
    result.findings=[]
    with pytest.raises(ValueError,match='omitted'):team.validate_boss(result,d)
    result.findings=[finding]
    result.investigation.hypotheses=[CausalHypothesis(id='h',explanation='Boss claims a cause without specialist evidence.',status='supported',supporting_refs=['frame'],distinguishing_test='Inspect control records.')]
    with pytest.raises(ValueError,match='invented a supported cause'):team.validate_boss(result,d)

@pytest.mark.asyncio
async def test_rejected_followup_does_not_replace_owner_or_expand_scope():
    from safety_app.agents.safety_reviewer.models import SafetyRequest
    from safety_app.domain.models import Observation
    rt=SimpleNamespace(config={},audit=None)
    obs=Observation(id='o',media_id='m',start_ms=0,end_ms=1,description='Fixture',evidence_refs=['frame'],method='human')
    team=SafetyTeam(rt,SimpleNamespace(payload={}),SafetyRequest(observations=[obs]),None,None)
    team.results=[SpecialistAssessment(specialist='applicability',question_id='app',call_id='app-call'),SpecialistAssessment(specialist='vehicles',question_id='q',call_id='first')]
    team.owners={'q':'vehicles'}
    task=SpecialistTask(question_id='q',specialist='vehicles',question='Check a targeted fact from this incident.',observation_ids=['o'],phase='follow_up',previous_call_id='wrong')
    with pytest.raises(ValueError,match='latest prior'):await team.delegate(task,SimpleNamespace(cancelled=lambda:False))
    assert team.owners=={'q':'vehicles'} and not team.calls

@pytest.mark.asyncio
@pytest.mark.parametrize('complete_challenge',[True,False])
async def test_real_delegation_followup_latest_answer_and_causal_challenge(tmp_path,complete_challenge):
    import shutil
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.workers.runner import execute
    from backend.tests.test_overhaul import footage
    project=Path(__file__).resolve().parents[1]
    shutil.copytree(project/'configs',tmp_path/'configs')
    rt=Runtime(tmp_path);frame=footage(rt)
    job=rt.jobs.start('facility_review','delegated-investigation-fixture',{'media_ids':['m']})
    obs={'id':'o','media_id':'m','start_ms':100,'end_ms':100,'description':'Synthetic fixture marker in zone.','evidence_refs':[frame.id],'method':'ai','uncertainty':['Fictional fixture only']}
    def finding(id):return Finding(id=id,review_id=job.run_id,question_id='q',specialist='vehicles',condition='Synthetic fixture marker in zone.',conclusion='Insufficient real-world evidence.',evidence_refs=[frame.id],uncertainty=['Fictional fixture only']).model_dump(mode='json')
    hypotheses=[{'id':'layout','explanation':'A layout issue may explain the apparent interaction.','status':'unresolved','supporting_refs':[frame.id],'missing_facts':['No actual site layout or sightline measurements.'],'distinguishing_test':'Measure sightlines along both paths.'},{'id':'motion','explanation':'Sampling may have omitted an avoidance action.','status':'unresolved','supporting_refs':[],'missing_facts':['No continuous before/after footage.'],'distinguishing_test':'Review continuous source-time footage.'}]
    final={'findings':[finding('latest-finding')],'missing_context':['No actual site investigation.'],'investigation':{'sequence':[{'media_id':'m','start_ms':100,'end_ms':100,'description':'Synthetic fixture marker is visible.','evidence_refs':[frame.id]}],'hypotheses':hypotheses,'cause_status':'not_established','limitations':['Fictional evidence cannot establish a real cause.']}}
    rounds=0;vehicle_calls=0
    async def boss(messages,info):
        nonlocal rounds
        rounds+=1
        if rounds==1:
            return ModelResponse(parts=[ToolCallPart('delegate_specialist',{'task':{'question_id':'app','specialist':'applicability','question':'Establish applicability for this fictional event.','observation_ids':['o']}},tool_call_id='app'),ToolCallPart('delegate_specialist',{'task':{'question_id':'q','specialist':'vehicles','question':'Assess the apparent vehicle interaction and evidence gaps.','observation_ids':['o']}},tool_call_id='initial')])
        if rounds==2:return ModelResponse(parts=[ToolCallPart('delegate_specialist',{'task':{'question_id':'q','specialist':'vehicles','question':'Test whether sightlines or missing motion evidence distinguish the alternatives.','observation_ids':['o'],'phase':'follow_up','previous_call_id':'delegation-2'}},tool_call_id='follow')])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,final)])
    def models(name):
        nonlocal vehicle_calls
        if name=='safety_reviewer':return FunctionModel(boss)
        if name=='video_observer':return TestModel(call_tools=[],custom_output_args={'observations':[obs]})
        if name=='specialist_applicability':return TestModel(call_tools=[],custom_output_args={'specialist':'applicability'})
        if name=='specialist_vehicles':
            vehicle_calls+=1
            return TestModel(call_tools=[],custom_output_args={'specialist':'vehicles','findings':[finding('initial-finding' if vehicle_calls==1 else 'latest-finding')],'hypotheses':hypotheses if vehicle_calls==2 else [],'missing_context':['No actual site investigation.']})
        if name=='challenge_reviewer':return TestModel(call_tools=[],custom_output_args={'checked_finding_ids':['latest-finding'],'checked_hypothesis_ids':['layout','motion'] if complete_challenge else []})
        raise AssertionError(name)
    result=await execute(rt,job,models)
    if complete_challenge:
        assert result.status=='completed',result.error
        review=rt.db.get('review',job.run_id)
        assert len(review['delegations'])==3 and vehicle_calls==2
        assert review['investigation']['cause_status']=='not_established'
        assert len(review['investigation']['hypotheses'])==2
        assert not any(f['id']=='initial-finding' for f in rt.db.list('finding'))
        assert len(rt.audit.rows(event_type='specialist_dispatched'))==3
        # Each specialist run is linked to its dispatch while it runs, for the live agent tree.
        dispatched={r['provenance']['call_id'] for r in rt.audit.rows(event_type='specialist_dispatched')}
        started=[r for r in rt.audit.rows(event_type='run_started') if (r.get('agent_name') or '').startswith('specialist_')]
        assert {r['provenance'].get('call_id') for r in started}==dispatched
    else:
        assert result.status=='failed' and 'every causal hypothesis' in result.error.message
        assert not rt.db.list('finding') and rt.db.list('coverage_gap')
    assert not rt.budgets.status()['outstanding']

def test_input_bound_counts_current_instructions_once_without_dropping_tool_evidence():
    from pydantic_ai.messages import ModelRequest,UserPromptPart,ToolReturnPart
    from safety_app.agents.shared.factory import input_bound
    prompt='Standing instructions.'
    history=[ModelRequest(parts=[UserPromptPart('Observation.')],instructions=prompt),ModelRequest(parts=[ToolReturnPart('delegate_specialist',{'evidence':'Keep this exact returned evidence.'},tool_call_id='call')],instructions=prompt)]
    bound,frames,size=input_bound(history)
    without=[ModelRequest(parts=h.parts,instructions=None) for h in history]
    assert bound==input_bound(without)[0]+len(prompt.encode())
    assert bound>len(prompt.encode())+len('Observation.') and frames==size==0

def test_additive_import_is_idempotent_and_does_not_refresh_camera_or_history(tmp_path,monkeypatch):
    from scripts import import_safety_library as importer
    db=Database(tmp_path/'data/app.sqlite')
    camera={'id':'camera-1','site_id':'local','calibration_version':'preserve-user-calibration'}
    history={'id':'history','message':'Preserve this historical record.'}
    db.save('camera',camera);db.save('review',history,immutable=True)
    row=SourceExcerpt(id='imported',source_id='doc',title='Fixture',section='1910.178',text='Complete fictional source block.',source_type='requirement',version='fixture-v1',site_id='local',jurisdiction='US-federal-OSHA')
    monkeypatch.setattr(importer,'records',lambda *args:[row])
    monkeypatch.setattr('sys.argv',['import_safety_library.py','unused-snapshot','--site-id','local','--root',str(tmp_path),'--apply'])
    importer.main();importer.main()
    assert db.get('camera','camera-1')==camera and db.get('review','history')==history
    assert len(db.list('source'))==1
    assert Sources(db).discover('fictional','US-federal-OSHA','',1,'local').candidates[0].id=='imported'


def test_graph_warnings_do_not_evict_requested_exact_clauses_that_fit(indexed):
    primary=['indexed-fixture-primary-'+str(i)+'x'*60 for i in range(3)]
    context=['indexed-fixture-context-'+str(i)+'x'*60 for i in range(9)]
    for id in context:store(indexed,id,'Full supporting context. '*120)
    originals={}
    for i,id in enumerate(primary):
        originals[id]=store(indexed,id,'Exact primary requirement '+str(i)+'. '*25,clause=f'1910.22({i})',related_ids=context[i*3:i*3+3])
    packet=indexed.fetch_packet(primary,'s',max_bytes=2400)
    assert set(primary)<={s.id for s in packet.excerpts}
    assert len(packet.model_dump_json().encode())<=2400 and not packet.complete
    assert any('Supporting context' in gap for gap in packet.gaps)
    assert any('context not verified' in gap for gap in packet.gaps)
    assert any('event-date applicability not established' in gap for gap in packet.gaps)
    assert all(s.text==originals[s.id].text for s in packet.excerpts if s.id in originals)
