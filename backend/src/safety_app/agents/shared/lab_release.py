"""Chat Lab block validation and rendering; no provider or storage effects."""
from safety_app.domain.chat_lab import LabAnswer, LabChallenge, LabClaimCheck

LABELS={'visual':'Observed in frames','general':'General guidance · not verified against retrieved sources', 'guidance':'Sourced guidance',
        'requirement':'Regulatory requirement','product':'Product documentation','record':'Saved review','uncertain':'Unresolved'}


def render_answer(claims, gaps=(), *, omitted=False):
    gaps=list(dict.fromkeys(x.strip() for x in gaps if x and x.strip()))
    substantive=any(c.kind!='uncertain' for c in claims)
    partial=bool(gaps or omitted or any(c.kind in ('uncertain','general') for c in claims))
    text='\n\n'.join((LABELS[c.kind]+': ' if c.kind=='general' else '')+c.text for c in claims)
    return LabAnswer(answer=text or 'I could not verify an answer from the available information. Please provide the relevant product detail, source, or saved review.',
        claims=claims,missing_context=gaps,block_rendered=True,
        outcome=('partial' if partial else 'answered') if substantive else 'clarification',
        completion=('partial' if partial else 'complete') if substantive else 'clarification')


def claim_errors(answer,evidence,applicability,task):
    errors={}
    ids=[c.id for c in answer.claims]
    if len(ids)!=len(set(ids)):return {'*':['duplicate_claim_ids']}
    allowed={'product':{'product','uncertain'},'records':{'record','uncertain'},'video':{'visual','record','requirement','guidance','general','uncertain'}}.get(task,{'requirement','guidance','general','uncertain'})
    for c in answer.claims:
        codes=[];sources=[evidence[id] for id in c.citation_ids if id in evidence]
        if c.kind not in allowed:codes.append('wrong_claim_category')
        if not set(c.citation_ids)<=evidence.keys():codes.append('unseen_citation')
        if c.kind not in ('uncertain','general') and not sources:codes.append('missing_citation')
        if c.kind=='general' and c.citation_ids:codes.append('general_has_citation')
        if c.kind in ('product','record') and any(s.kind!=c.kind for s in sources):codes.append('wrong_source_category')
        if c.kind=='visual' and any(s.kind!='visual' or not s.provenance.get('inspected') for s in sources):codes.append('pixels_not_inspected')
        if c.kind=='guidance' and any(s.kind not in ('guidance','requirement','procedure','site_rule','web_summary') for s in sources):codes.append('wrong_source_category')
        if c.kind=='requirement':
            if not applicability or not applicability.requirements_allowed:codes.append('applicability_unresolved')
            if not any(s.kind=='requirement' and s.context_complete and s.clause==c.exact_clause and s.effective_date for s in sources):codes.append('requirement_context_missing')
        if c.id in c.depends_on or not set(c.depends_on)<=set(ids):codes.append('invalid_dependency')
        if codes:errors[c.id]=codes
    return errors


def release_blocks(answer,assessment,errors,provisional_ids=()):
    """Only independently publishable reviewed blocks survive; dependencies are closed."""
    ids={c.id for c in answer.claims};checks={c.claim_id:c for c in assessment.claim_checks}
    global_failure=(len(checks)!=len(assessment.claim_checks) or set(checks)!=ids or '*' in errors)
    rejected=set(errors)
    for issue in assessment.blocking_issues:
        if issue.claim_id is None or issue.claim_id not in ids:global_failure=True
        else:rejected.add(issue.claim_id)
    for c in answer.claims:
        check=checks.get(c.id)
        good=check and check.independently_publishable and (check.verdict=='supported' or check.verdict=='general' and c.kind=='general' or check.verdict=='uncertain' and (c.kind=='uncertain' or c.kind=='guidance' and c.id in provisional_ids))
        if not good:rejected.add(c.id)
    if global_failure:rejected=ids.copy()
    while True:
        new={c.id for c in answer.claims if set(c.depends_on)&rejected}
        if new<=rejected:break
        rejected|=new
    kept=[c for c in answer.claims if c.id not in rejected]
    codes=list(dict.fromkeys([code for values in errors.values() for code in values]+[i.code for i in assessment.blocking_issues]+(['review_coverage_invalid'] if global_failure else [])+(['blocks_withheld'] if rejected else [])))
    result=LabChallenge(approved=not rejected,checked_claim_ids=list(checks)[:16],failure_codes=codes,
        observations=['Some sourced guidance remains provisional; its source and applicability limitations still apply.'] if any(c.kind=='guidance' and checks[c.id].verdict=='uncertain' for c in kept) else [],
        claim_checks=[LabClaimCheck(claim_id=c.claim_id,verdict=c.verdict,independently_publishable=c.independently_publishable,note='Block released after review.' if c.claim_id not in rejected else 'Block withheld after review.') for c in assessment.claim_checks[:16]],
        published_claim_ids=[c.id for c in kept],omitted_claim_ids=[c.id for c in answer.claims if c.id in rejected],
        unresolved_claims=[c.id for c in kept if c.kind=='uncertain'],
        issues=['Some blocks were withheld because their support or necessary context could not be confirmed.'] if rejected else [])
    return kept,result
