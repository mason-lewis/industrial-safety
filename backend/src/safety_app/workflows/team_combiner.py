"""After every finding of a run has a terminal team review, the team combiner writes the manager brief.

The brief is optional decoration of the report: it can only select supplied case IDs and copy supplied
actions. It shares the run's budget workflow, and any failure leaves the deterministic summary in place.
"""
import json
from safety_app.modules.safety_review.review_runs import ReviewRuns

TERMINAL={'completed','failed','cancelled','budget_exceeded','interrupted'}


def ready(rt, run_id):
    """True when the run ended, every window is settled and every handed-off case review is terminal."""
    try:run=rt.db.get('review_run',run_id)
    except KeyError:return False
    # Only runs created with the combiner enabled qualify; historical runs never start new paid work.
    if not (run.get('configuration') or {}).get('review_team',{}).get('team_combiner'):return False
    if run.get('state')!='ended':return False
    if any(w.get('status') in ('queued','running') for w in rt.db.list('review_window') if w.get('run_id')==run_id):return False
    cases=[c for c in rt.db.list('video_safety_case') if c['analysis_id']==run_id]
    if not cases:return False
    handoffs={h['case_id']:h for h in rt.db.list('safety_handoff')}
    for case in cases:
        handoff=handoffs.get(case['id'])
        if handoff and rt.jobs.get(handoff['review_job_id']).status not in TERMINAL:return False
    return True


def start_ready_summaries(rt, run_ids=None):
    """Idempotent outbox: one team_summary job per run, never repeated."""
    if rt.budgets.state('stopped')=='1':return []
    started=[]
    ids=run_ids if run_ids is not None else {c['analysis_id'] for c in rt.db.list('video_safety_case')}
    for run_id in ids:
        if not ready(rt,run_id):continue
        run=rt.db.get('review_run',run_id);parent=rt.jobs.get(run['job_id'])
        payload={'review_run_id':run_id,'parent_job_id':parent.id,'budget_workflow_id':run_id,'media_ids':[run['media_id']],
                 'start_ms':run['start_ms'],'end_ms':run['end_ms'],'mode':(run.get('request') or {}).get('mode','hosted')}
        job=rt.jobs.start('team_summary','team-summary:'+run_id,payload,parent.site_id)
        started.append(job.id)
    return started


def brief_input(rt, run_id, site_id):
    status=ReviewRuns(rt).status(run_id,site_id)
    decisions={d.target_id:d.decision for d in status.decisions}
    cases=[]
    for item in status.safety.cases:
        findings=item.assessment.findings if item.assessment else []
        cases.append({'case_id':item.case.id,'category':item.case.category,'priority':item.priority,'start_ms':item.case.start_ms,
            'status':item.status,'team_reviewed':bool(item.assessment),
            'conclusions':[{'status':f.status,'classification':f.classification,'conclusion':f.conclusion,'uncertainty':f.uncertainty[:2]} for f in findings][:3],
            'screening':[s.check.condition for s in item.screenings][:2],
            'actions':[a.action for f in findings for a in f.corrective_actions][:4]+[r for f in findings for r in f.recommendations][:2],
            'human_decision':decisions.get(item.case.id)})
    return {'run_kind':status.run.kind,'coverage':{'completed_windows':status.completed_windows,'total_windows':status.total_windows,
            # pending_specialists also counts moments left out of a case's bounded team packet; keep them apart.
            'complete':status.coverage_complete,'pending_team_reviews':sum(1 for item in status.safety.cases if item.assessment is None),
            'moments_not_in_team_packets':sum(len(item.unassessed_screening_ids) for item in status.safety.cases),
            'unclear_checks':sum(1 for s in status.safety.screenings if s.check.result=='unknown')},'cases':cases}


def fixture_model(case_ids):
    from pydantic_ai.models.test import TestModel
    return TestModel(call_tools=[],custom_output_args={'summary':'Demo summary from fixed fixture responses. These findings do not assess this video.',
        'priority_case_ids':case_ids[:5],'actions':[],'confidence_note':'Fixture run: no real safety judgment was made.'})


async def combine(rt, job, model_factory=None):
    from safety_app.workflows.agent_stage import run_stage,TaskInput
    from safety_app.agents.safety_reviewer.combiner import create
    run_id=job.payload['review_run_id']
    data=brief_input(rt,run_id,job.site_id)
    ids={c['case_id'] for c in data['cases']}
    if job.payload.get('mode')=='fixture' and model_factory is None:
        model_factory=lambda name:fixture_model(sorted(ids))
    def setup(deps):
        def check(result):
            unknown=[i for i in result.priority_case_ids if i not in ids]
            if unknown:raise ValueError(f'Unknown case IDs {unknown}; select only supplied case_id values')
        deps.output_check=check
    stage=job.model_copy(update={'run_id':run_id})
    # Use the run's frozen caps (as case reviews do): the run's shared request count already exceeds
    # the global default allowance once screening and every case review have finished.
    from safety_app.workflows.full_review import frozen_runtime
    from safety_app.modules.safety_review.review_runs import ReviewRuns
    scoped=frozen_runtime(rt,ReviewRuns(rt).get(run_id,job.site_id))
    result,deps=await run_stage(scoped,stage,'team_combiner',TaskInput(data),model_factory,setup=setup,agent_builder=create,text_only=True,
        content_builder=lambda rt,job,name,request,deps,text_only:[request.model_dump_json()],settings_override={'openai_reasoning_effort':'low'})
    record={'id':run_id,'run_id':run_id,'agent_run_id':deps.run_id,**result.model_dump(mode='json')}
    rt.db.save('team_brief',record)
    rt.audit.write('team_brief_saved',run_id=run_id,job_id=job.id,output={'priority_case_ids':result.priority_case_ids,'actions':len(result.actions)})
    return [run_id]
