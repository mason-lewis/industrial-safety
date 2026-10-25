"""Lossless output-boundary normalization, separate from evidence validation."""
import re
from pydantic import field_validator
from safety_app.domain.base import StrictModel

def clause_id(value):
    if not isinstance(value,str):return value
    value=re.sub(r'^(?:29\s+CFR\s+)?(?:§\s*)?','',value.strip(),flags=re.I)
    compact=re.sub(r'\s+','',value)
    return compact if re.fullmatch(r'\d+\.\d+(?:\([a-zA-Z0-9]+\))*',compact) else value

def references(value):
    if not isinstance(value,list) or not all(isinstance(v,str) for v in value):return value
    return sorted(set(v.strip() for v in value))

class AgentOutput(StrictModel):
    @field_validator('*',mode='before')
    @classmethod
    def normalize_boundary(cls,value,info):
        name=info.field_name
        if name=='exact_clause':return clause_id(value)
        if name.endswith('_refs') or (name.startswith('checked_') and name.endswith('_ids')):return references(value)
        if name in ('id','question_id','call_id','claim_id','finding_id','hypothesis_id','previous_call_id') and isinstance(value,str):return value.strip()
        return value

def retain_source_gaps(authored,known):
    remaining=[]
    for entry in authored:
        plain=entry
        for prefix in ('Prepared packet gap: ','Fetched gap: '):plain=plain.removeprefix(prefix)
        if plain in known or plain.removesuffix('.') in known:continue
        remaining.append(entry)
    return list(dict.fromkeys([*sorted(known),*remaining]))

def comparison_record(value):
    """Compare formatting only; never paraphrase, case-fold prose or change IDs."""
    if isinstance(value,dict):
        return {k:references(v) if k.endswith('_refs') else clause_id(v) if k=='exact_clause' else comparison_record(v) for k,v in value.items()}
    if isinstance(value,list):return [comparison_record(v) for v in value]
    if isinstance(value,str):return ' '.join(value.split())
    return value
