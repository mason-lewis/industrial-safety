"""Preserve supplied visual qualifications without inventing legal evidence."""


def preserve_visual_gaps(finding):
    if (finding.uncertainty or finding.source_refs or finding.rule_refs or finding.exact_clause
        or finding.classification not in ('general_hazard','insufficient_evidence')):
        return False
    gaps=[e.explanation for e in finding.element_checks if e.status=='unknown']
    # This is a fact about the empty source set, not a model-generated requirement.
    gaps.append('No external requirement was retrieved; regulatory applicability is not established.')
    finding.uncertainty=list(dict.fromkeys(gaps))
    return True
