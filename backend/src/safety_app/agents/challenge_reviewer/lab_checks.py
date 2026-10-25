"""Deterministic release rules for the independent reviewer's typed findings."""
from safety_app.domain.chat_lab import LabAnswer, LabChallenge, LabChallengeAssessment


ISSUE_LABELS = {
    'unsupported_advice': 'Unsupported advice',
    'contradiction': 'Contradiction with supplied evidence',
    'missing_claim': 'Narrative claim missing from structured claims',
    'unverified_requirement': 'Requirement lacks verified applicability or text',
    'scope_violation': 'Reply crosses the product scope',
    'citation_mismatch': 'Citation does not support the claim',
    'record_uncertainty_lost': 'Saved-review uncertainty or human decision was lost',
}


def evaluate_challenge(answer: LabAnswer, assessment: LabChallengeAssessment) -> LabChallenge:
    """Require complete coverage and no substantive defect; never rewrite the draft."""
    claims = {claim.id: claim for claim in answer.claims}
    checked = [check.claim_id for check in assessment.claim_checks]
    codes, issues = [], []

    def block(code, message):
        codes.append(code)
        issues.append(message)

    if len(checked) != len(set(checked)):
        block('duplicate_claim_check', 'Independent review returned duplicate claim checks.')
    if set(checked) - claims.keys():
        block('unknown_claim_check', 'Independent review returned an unknown claim ID.')
    if claims.keys() - set(checked):
        block('missing_claim_check', 'Independent review did not check every claim.')
    for check in assessment.claim_checks:
        claim = claims.get(check.claim_id)
        if claim is None:
            continue
        if check.verdict in ('unsupported', 'contradicted'):
            block(check.verdict, f'Claim {claim.id}: {check.verdict} by the supplied evidence.')
        elif check.verdict == 'uncertain' and claim.kind != 'uncertain':
            block('unresolved_support', f'Claim {claim.id}: support remains unresolved.')
    for issue in assessment.blocking_issues:
        if issue.claim_id is not None and issue.claim_id not in claims:
            block('unknown_issue_claim', 'Independent review attached a defect to an unknown claim.')
        else:
            block(issue.code, ISSUE_LABELS[issue.code] + (f' ({issue.claim_id}).' if issue.claim_id else '.'))
    # Expose bounded category labels on failure, never unchecked advice quoted in notes.
    approved = not codes
    return LabChallenge(
        approved=approved, checked_claim_ids=list(dict.fromkeys(checked))[:16],
        issues=issues[:16], failure_codes=codes[:40],
        unresolved_claims=[claim.id for claim in answer.claims if claim.kind == 'uncertain'],
        claim_checks=assessment.claim_checks if approved else [],
        observations=assessment.observations if approved else [],
    )


class LabReviewBlocked(ValueError):
    """A completed review held the answer; this is distinct from a provider failure."""

    def __init__(self, challenge: LabChallenge):
        self.challenge = challenge
        super().__init__('Independent review held the answer: ' + '; '.join(challenge.issues[:3]))
