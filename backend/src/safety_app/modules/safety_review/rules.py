from safety_app.domain.base import StrictModel as BaseModel
"""Versioned site requirements. Missing rules remain explicit."""
from safety_app.domain.models import SiteRule
class SiteRuleSet(BaseModel):
    rules:list[SiteRule]
    missing_topics:list[str]
class Rules:
    def __init__(self,db):self.db=db
    def matching(self,site_id,topic):
        values=[SiteRule.model_validate(r) for r in self.db.list('rule') if r['site_id']==site_id and (not topic or topic.lower() in r['description'].lower())]
        return SiteRuleSet(rules=values,missing_topics=[] if values else [topic or 'No site rules supplied'])
