"""Review cache identity includes evidence, policy, models, and agent prompts."""
import hashlib
import json
from pathlib import Path

from safety_app.modules.safety_review.catalog import OWNERS, PROFILES


def cache_key(rt, job):
    package_root = Path(__file__).resolve().parents[1]
    media_ids = job.payload['media_ids']
    identity = {
        'workflow_version': 'delegated-investigation-v5-scoped-detail',
        'team': rt.config.get('review_team', {}),
        'profiles': PROFILES,
        'owners': OWNERS,
        'payload': job.payload,
        'media': [rt.db.get('media', media_id) for media_id in media_ids],
        'frames': [
            (frame['id'], frame['content_hash'])
            for frame in rt.db.list('frame') if frame['media_id'] in media_ids
        ],
        'sources_revision': rt.sources.revision(),
        'rules': rt.db.list('rule'),
        'classes': rt.registry.model_dump(),
        'models': rt.config['agents'],
        'aliases': rt.config['aliases'],
        'prompts': {
            str(path.relative_to(package_root)): path.read_text()
            for path in (package_root / 'agents').rglob('*prompt.md')
        },
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
