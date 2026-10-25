"""Prepare configuration and guidance. Sample footage is never imported on startup."""
import shutil
from pathlib import Path


def prepare_demo(root, project):
    root=Path(root);project=Path(project)
    if root!=project and not (root/'configs').exists():
        root.mkdir(parents=True,exist_ok=True)
        shutil.copytree(project/'configs',root/'configs')
    folder=root/'data/input';folder.mkdir(parents=True,exist_ok=True)
    from safety_app.infrastructure.runtime import Runtime
    rt=Runtime(root)
    bundle=project/'demo/safety-sources.ndjson'
    if bundle.exists():
        from safety_app.domain.models import SourceExcerpt
        for line in bundle.read_text().splitlines():
            if line.strip():rt.db.save('source',SourceExcerpt.model_validate_json(line),immutable=True)
    return rt
