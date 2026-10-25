# Agent output contracts

`models.py` describes structured data: fields, types, allowed statuses, required
content and bounds. Pydantic validates structure. It cannot establish that a
claim is true, an event occurred, a source applies or a cause is supported.
Those decisions also require scoped evidence checks and independent challenge.

The output boundary now separates three responsibilities:

| Responsibility | Implementation |
| --- | --- |
| Structural validation | Agent Pydantic models and generated API contracts; extra fields and invalid categories remain errors. |
| Deterministic bookkeeping | Python retains source warnings, binds chat claim IDs to validated content/question, and attaches the latest specialist claim list to the boss answer. |
| Evidence validation | Workflow checks retrieved references, clause identity, ownership, applicability, observations, corrective actions and causal support. |

`agents/shared/contracts.py` normalizes reference whitespace/duplicates/order,
common citation prefixes (`29 CFR`, `§`) and citation spacing in agent output
contracts. Checked-ID lists are sets of coverage IDs, not narrative order.
It does not repair unknown IDs, reinterpret descendant clauses, normalize
classification categories or rewrite the exact source text. Finding/cause
comparison tolerates whitespace and reference-set order while preserving
claim meaning, evidence references, statuses and identities.

For Safety Chat, specialist claims are validated before Python assigns stable
IDs from the question and claim content. A blank returned question ID is bound
to the assigned task; a conflicting question/specialist is rejected. The boss
may leave claims empty; Python attaches every latest validated specialist claim
and warning. If the boss supplies claims, changed or invented claims are still
rejected. The final/challenge schema supports the bounded eight assessments
times four claims (32). Independent challenge must cover the resulting IDs and
check unsupported statements in the boss's prose.

For an uncertain chat claim with no exact_clause, a reference to a source that
the scoped source service explicitly omitted for the packet byte budget is
moved from source_refs into a missing-evidence warning. Python retains the claim
text and uncertain category, records chat_gap_reference_retained, and validates
all remaining references before accepting the assessment. The omitted text
does not become a citation or evidence for the boss/challenger. This narrow
bookkeeping rule does not apply to requirement/guidance claims, unknown IDs,
preview-only sources, scope/date failures or unseen rule references. A later
successful fetch makes the source eligible for normal evidence validation.

For event/facility review, Python retains service/specialist warnings. Findings
and supported causes still require correspondence with delegated assessments;
only cosmetic comparison differences are normalized. Observer footage checks,
annotation geometry/approval rules, source permissions, event-date requirements
and human decisions retain their existing strict validations.

Output validation feedback lists chat claim errors together with relevant
stored citation metadata. After the one allowed correction, a substantive
validation failure becomes `AgentOutputRejected` naming the stage and recorded
reason. Raw structured-output parsing failures remain provider/framework errors
when no workflow validation reason was recorded. Failed answers remain failed,
with their audits and costs retained; no successful answer is fabricated.

Regression coverage includes prefixed citations, reordered references, missing
claim IDs, omitted bookkeeping fields, 32 claims, batched retrieval, deadlines,
parent/descendant mismatches, changed claim meaning, foreign ownership/evidence
and a full delegated correction followed by independent challenge. Fixtures
test mechanics and failure handling, not the quality of safety judgments.

Remaining limitations: free prose is still model-generated; a schema cannot
prove semantic accuracy. Evidence sufficiency and retrieval quality need
scenario evaluation. Token/tool/deadline limits can still interrupt a review.
Provider quota errors require account/quota handling, separate from contracts.
