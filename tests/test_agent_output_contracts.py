"""Realistic formatting variations must not relax evidence requirements."""
from types import SimpleNamespace
import pytest
from safety_app.agents.safety_reviewer.chat_models import ChatClaim,ChatAssessment,ChatAnswer,ChatTask,ChatChallenge
from safety_app.agents.shared.contracts import clause_id,comparison_record
from safety_app.workflows.safety_chat import ChatTeam

@pytest.mark.parametrize('value',['1910.178(m)(2)',' 29 CFR 1910.178 (m) (2) ','§ 1910.178(m)(2)','29 CFR § 1910.178(m)(2)'])
def test_citation_formatting_has_one_identity(value):
    assert clause_id(value)=='1910.178(m)(2)'
    assert clause_id('1910.178(l)(1)(i)')!='1910.178(l)'

def test_reasoning_metadata_is_visible_but_hidden_text_is_still_redacted():
    from safety_app.infrastructure.audit import redact
    assert redact({'reasoning_tokens':2000,'openai_reasoning_effort':'low','reasoning':'hidden text'})=={'reasoning_tokens':2000,'openai_reasoning_effort':'low','reasoning':'[REDACTED]'}
    assert redact({'reasoning_tokens':'hidden text','openai_reasoning_effort':{'text':'hidden'}})=={'reasoning_tokens':'[REDACTED]','openai_reasoning_effort':'[REDACTED]'}

def test_reference_sets_normalize_but_foreign_ids_and_statuses_are_not_repaired():
    claim=ChatClaim(text='Scenario remains unverified.',kind='uncertain',source_refs=[' b ','a','a'],exact_clause='29 CFR § 1910.178 (m) (2)')
    assert claim.source_refs==['a','b'] and claim.exact_clause=='1910.178(m)(2)'
    assert ChatChallenge(checked_claim_ids=[' c ','c']).checked_claim_ids==['c']
    assert comparison_record({'text':'must stop'})!=comparison_record({'text':'may stop'})
    assert comparison_record({'source_refs':['a']})!=comparison_record({'source_refs':['foreign']})
    with pytest.raises(ValueError):ChatClaim(text='Claim',kind='Requirement')

def test_claim_identifiers_are_bound_by_python_and_boss_retains_all_claims():
    team=ChatTeam(SimpleNamespace(config={}),SimpleNamespace(),None)
    d=SimpleNamespace(source_gaps={'Date unverified.'},seen_sources=set(),seen_rules=set())
    task=ChatTask(question_id='scope',specialist='applicability',question='Determine scenario coverage.')
    child=ChatAssessment(specialist='applicability',answer='Unknown.',claims=[ChatClaim(text='Coverage is unresolved.',kind='uncertain')])
    team.validate_child(child,d,task)
    assert child.question_id=='scope' and child.claims[0].id.startswith('claim-')
    team.results=[child]
    boss=ChatAnswer(answer='Coverage is unresolved.')
    team.validate_boss(boss,d)
    assert boss.claims==child.claims and boss.claims[0] is not child.claims[0]
    assert boss.missing_context==['Date unverified.']
    foreign=child.model_copy(update={'question_id':'foreign'})
    with pytest.raises(ValueError,match='identity'):team.validate_child(foreign,d,task)

def test_all_eight_specialist_results_fit_final_contract_and_challenge():
    claims=[ChatClaim(id=f'claim-{i}',text=f'Unresolved fact {i}',kind='uncertain') for i in range(32)]
    answer=ChatAnswer(answer='Bounded synthesis.',claims=claims)
    assert len(ChatAnswer.model_validate_json(answer.model_dump_json()).claims)==32
    challenge=ChatChallenge(checked_claim_ids=[c.id for c in claims])
    assert len(challenge.checked_claim_ids)==32

def test_event_warning_retention_and_comparison_do_not_change_evidence():
    from safety_app.agents.safety_reviewer.models import SpecialistAssessment,SpecialistTask,SafetyRequest,SafetyResult
    from safety_app.workflows.safety_team import SafetyTeam,signature
    from safety_app.domain.models import Finding
    team=SafetyTeam(SimpleNamespace(config={}),SimpleNamespace(payload={}),SafetyRequest(observations=[]),None,None)
    d=SimpleNamespace(source_gaps={'Source date unverified.'},seen_frames=set(),seen_sources=set(),seen_rules=set())
    task=SpecialistTask(question_id='q',specialist='applicability',question='Determine review applicability.',observation_ids=['obs'])
    result=SpecialistAssessment(specialist='applicability')
    team.validate_child(result,d,task)
    result.question_id='q';team.results=[result]
    boss=SafetyResult();team.validate_boss(boss,d)
    assert boss.missing_context==['Source date unverified.']
    finding=Finding(review_id='review',condition='A visible condition.',conclusion='Unknown',evidence_refs=['a','b'])
    formatted=finding.model_copy(update={'condition':' A  visible condition. ','evidence_refs':['b','a','a']})
    assert signature(finding)==signature(formatted)
    assert signature(finding)!=signature(finding.model_copy(update={'conclusion':'A violation occurred'}))
