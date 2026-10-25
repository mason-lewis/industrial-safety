"""Recorded hosted failure: context questions must not require invented safety actions."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from safety_app.agents.safety_reviewer.models import AgentFinding, SpecialistAssessment, SpecialistTask
from safety_app.domain.models import Finding
from safety_app.workflows.safety_team import SafetyTeam, validate_finding


def test_recorded_context_request_has_a_valid_non_action_answer_path():
    raw=json.loads((Path(__file__).parent/'fixtures/balcony_applicability_context_request.json').read_text())
    # Preserve historical reading, while new agent schemas prohibit the ambiguous field.
    original=Finding.model_validate(raw['findings'][0])
    with pytest.raises(ValidationError,match='recommendations'):
        SpecialistAssessment.model_validate(raw)
    questions=raw['findings'][0]['recommendations']
    corrected=SpecialistAssessment(specialist='applicability',question_id=raw['question_id'],
        applicability_checks=original.element_checks,
        missing_context=[*raw['missing_context'],*questions])
    deps=SimpleNamespace(seen_frames=set(original.evidence_refs),seen_sources=set(original.source_refs),seen_rules=set(),source_gaps=set())
    task=SpecialistTask(question_id=raw['question_id'],specialist='applicability',question='Establish applicability using available context.',observation_ids=['observation'])
    team=SimpleNamespace(job=SimpleNamespace(payload={'event_id':original.event_id}))
    SafetyTeam.validate_child(team,corrected,deps,task)
    assert all(q in corrected.missing_context for q in questions)
    assert not corrected.findings  # No invented failed control or legal finding.
    assert all(c.status=='unknown' for c in corrected.applicability_checks)
    corrected.applicability_checks[0].evidence_refs=['another-review-frame']
    with pytest.raises(ValueError,match='unseen'):
        SafetyTeam.validate_child(team,corrected,deps,task)


def test_new_safety_actions_remain_specific_and_evidence_scoped():
    deps=SimpleNamespace(seen_frames={'f1'},seen_sources=set(),seen_rules=set())
    finding=AgentFinding(review_id='review',condition='Fixture obstruction',conclusion='Fixture hazard',
        classification='general_hazard',evidence_refs=['f1'],uncertainty=['Fixture only'],
        corrective_actions=[{'failed_control':'Fixture route obstructed','action':'Remove the fixture obstruction from this route',
                             'verification':'Inspect this route after removal','evidence_refs':['f1']}])
    validate_finding(finding,deps)
    finding.corrective_actions[0].evidence_refs=['foreign']
    with pytest.raises(ValueError,match='this finding evidence'):validate_finding(finding,deps)
    finding.corrective_actions[0].evidence_refs=['f1'];finding.corrective_actions[0].action='retrain employees'
    with pytest.raises(ValueError,match='Generic corrective'):validate_finding(finding,deps)
    assert AgentFinding.model_json_schema()['properties']['recommendations']['maxItems']==0


def test_unassessable_ppe_stays_unknown_without_a_fabricated_item_or_retry():
    from safety_app.domain.video_safety import VisualSafetyCheck
    check=VisualSafetyCheck(category='ppe',result='clear',visibility='partial',ppe_state='not_assessable',
        condition='No PPE requirements supplied; item details are not assessable.',
        start_ms=0,end_ms=100,evidence_refs=['frame'])
    assert check.result=='unknown' and check.ppe_item is None and not check.needs_judgment


def test_recorded_boss_causal_gap_preserves_findings_without_claiming_cause():
    from safety_app.agents.safety_reviewer.models import SafetyRequest,SafetyResult
    raw=json.loads((Path(__file__).parent/'fixtures/balcony_boss_causal_gap.json').read_text())
    team=SafetyTeam(SimpleNamespace(config={}),SimpleNamespace(payload={}),SafetyRequest(observations=[]),None,None)
    team.results=[SpecialistAssessment.model_validate(raw['specialist'])]
    deps=SimpleNamespace(seen_frames=set(raw['frames']),seen_sources=set(),seen_rules=set(),
        interval=lambda *args:None,media=SimpleNamespace(db=SimpleNamespace(get=lambda kind,id:raw['frames'][id])))
    result=SafetyResult.model_validate(raw['final'])
    original=result.findings[0].model_dump()
    assert any(h.status=='supported' and h.missing_facts for h in result.investigation.hypotheses)
    team.validate_boss(result,deps)
    assert result.findings[0].model_dump()==original
    assert result.investigation.cause_status=='not_established'
    assert all(h.status!='supported' for h in result.investigation.hypotheses)
    assert any('remains unresolved' in gap for gap in result.investigation.limitations)


def test_boss_selects_approved_records_by_id_without_rewriting_or_widening_scope():
    from safety_app.agents.safety_reviewer.models import SafetyRequest,SafetyResult
    raw=json.loads((Path(__file__).parent/'fixtures/balcony_boss_causal_gap.json').read_text())
    team=SafetyTeam(SimpleNamespace(config={}),SimpleNamespace(payload={}),SafetyRequest(observations=[]),None,None)
    assessment=SpecialistAssessment.model_validate(raw['specialist']);team.results=[assessment]
    deps=SimpleNamespace(seen_frames=set(raw['frames']),seen_sources=set(),seen_rules=set())
    finding=assessment.findings[0]
    result=SafetyResult(finding_ids=[finding.id],investigation={'limitations':['Cause is not established.']})
    team.validate_boss(result,deps)
    assert result.findings[0].model_dump()==finding.model_dump()
    result.findings[0].uncertainty.append('Independent check')
    assert 'Independent check' not in finding.uncertainty
    with pytest.raises(ValueError,match='unknown specialist'):
        team.validate_boss(SafetyResult(finding_ids=['another-review']),deps)
    with pytest.raises(ValueError,match='repeated'):
        team.validate_boss(SafetyResult(finding_ids=[finding.id,finding.id]),deps)
    with pytest.raises(ValueError,match='leave findings empty'):
        team.validate_boss(SafetyResult(finding_ids=[finding.id],findings=[finding]),deps)
    with pytest.raises(ValueError,match='omitted'):
        team.validate_boss(SafetyResult(),deps)


def test_omitted_event_id_is_bound_by_python_but_foreign_events_are_rejected():
    raw=json.loads((Path(__file__).parent/'fixtures/balcony_boss_causal_gap.json').read_text())
    result=SpecialistAssessment.model_validate(raw['specialist']);result.findings[0].event_id=None
    task=SpecialistTask(question_id=result.question_id,specialist=result.specialist,
        question='Review only the assigned observed condition.',observation_ids=['observation'])
    team=SimpleNamespace(job=SimpleNamespace(payload={'event_id':'assigned-event'}))
    deps=SimpleNamespace(seen_frames=set(raw['frames']),seen_sources=set(),seen_rules=set(),source_gaps=set())
    SafetyTeam.validate_child(team,result,deps,task)
    assert result.findings[0].event_id=='assigned-event'
    result.findings[0].event_id='foreign-event'
    with pytest.raises(ValueError,match='event mismatch'):SafetyTeam.validate_child(team,result,deps,task)


def test_missing_duplicate_conclusion_uses_the_condition_verbatim():
    condition='Material appears near the worker; exact contact remains uncertain.'
    finding=AgentFinding(review_id='review',condition=condition,evidence_refs=['frame'],uncertainty=['Unverified fixture.'])
    assert finding.conclusion==condition
    assert AgentFinding(review_id="review",conclusion=condition,evidence_refs=["frame"]).condition==condition
    with pytest.raises(ValidationError,match='substantive'):
        AgentFinding(review_id='review',evidence_refs=['frame'])
