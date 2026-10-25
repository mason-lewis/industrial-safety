"""Shared service composition for API and workers. Does not enable paid calls."""
import json
from pathlib import Path
from safety_app.infrastructure.repositories import Database
from safety_app.infrastructure.audit import Audit
from safety_app.infrastructure.budgets import BudgetLedger
from safety_app.infrastructure.jobs import Jobs
from safety_app.infrastructure.media_store import MediaStore
from safety_app.domain.models import CameraProfile,ClassRegistry,SourceExcerpt,SiteRule
class Runtime:
    def __init__(self,root):
        self.root=Path(root);self.config=json.loads((self.root/'configs/agents.yaml').read_text())
        from app.agents.model_policy import validate_agent_models
        validate_agent_models(self.config)
        self.db=Database(self.root/'data/app.sqlite');self.audit=Audit(self.root/'data/audit');self.budgets=BudgetLedger(self.db,self.audit,self.config);self.jobs=Jobs(self.db,self.audit);self.media=MediaStore(self.root,self.db)
        for path in (self.root/'configs/cameras').glob('*.yaml'):
            profile=CameraProfile.model_validate_json(path.read_text())
            if profile.view_type=='driver_pov' and profile.zones:raise ValueError('POV profiles cannot use fixed image zones')
            self.db.save('camera',profile)
        self.registry=ClassRegistry.model_validate_json((self.root/'configs/classes.yaml').read_text())
        self.db.save('class_registry',{'id':self.registry.version,'registry':self.registry.model_dump(mode='json')},immutable=True)
        for path in (self.root/'configs/sites').glob('*.yaml'):
            document=json.loads(path.read_text())
            for s in document.get('sources',[]):self.db.save('source',SourceExcerpt.model_validate(s),immutable=True)
            for r in document.get('rules',[]):self.db.save('rule',SiteRule.model_validate(r),immutable=True)
        from safety_app.modules.safety_review.sources import Sources
        self.sources=Sources(self.db)
        from safety_app.modules.monitoring.people import PeopleEvidence
        self.people=PeopleEvidence(self.root,self.db,self.media)
        from safety_app.modules.monitoring.preview import PersonPreviewService
        self.preview=PersonPreviewService(self.root,self.people)
    def camera(self,id):return CameraProfile.model_validate(self.db.get('camera',id))
    def classes(self,version):
        try:return ClassRegistry.model_validate(self.db.get('class_registry',version)['registry'])
        except KeyError:raise ValueError('Unknown class registry version')
_default=None
def runtime(root=None):
    global _default
    if root is not None:return Runtime(root)
    if _default is None:
        from safety_app.infrastructure.environment import load_environment,workspace_root
        load_environment()
        _default=Runtime(workspace_root())
    return _default
