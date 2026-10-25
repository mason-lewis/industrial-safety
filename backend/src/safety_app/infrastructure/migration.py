"""Repeatable additive import. Originals and their IDs stay intact."""
import hashlib
import sqlite3
import json
from pathlib import Path
from safety_app.domain.models import MediaRef,CandidateEvent,SourceExcerpt

def migrate(rt,legacy_repository=None,legacy_evidence=None):
    if legacy_repository is None:
        from app.storage.repository import Repository
        legacy_repository=Repository(rt.root/'data/artifacts/runs/lab.sqlite')
    if legacy_evidence is None:
        from app.evidence.store import EvidenceStore
        legacy_evidence=EvidenceStore(rt.root/'data/artifacts/evidence')
    count=0
    for record in legacy_repository.list():
        kind=record.pop('record_kind');id=record.get('id')
        if not id:continue
        rt.db.save('legacy_'+kind,record);count+=1
        # Import original event identity and references, without replacing legacy history.
        if kind=='candidate':
            rt.db.save('event',CandidateEvent(id=id,camera_id=record['camera_id'],event_type=record['kind'],start_ms=record['timestamp_ms'],end_ms=record['evidence_cutoff_ms'],producer_version='legacy-cv',status=record['state'],legacy_id=id))
    for ref,_ in list(legacy_evidence.records.values()):
        mid=f'{ref.session_id}-{ref.run_generation}'
        with rt.db.connect() as db:
            if db.execute("SELECT 1 FROM trial_hidden_records WHERE (kind='media' AND id=?) OR (kind='frame' AND id=?)",(mid,f'{mid}-{ref.id}')).fetchone():continue
        frames=[r for r,_ in legacy_evidence.records.values() if r.session_id==ref.session_id and r.run_generation==ref.run_generation]
        stamps=[r.timestamp_ms for r in frames]
        raw=legacy_evidence.image(ref.id)
        rt.db.save('media',MediaRef(id=mid,media_id=mid,source_id=ref.source_id,camera_id=ref.camera_id,start_ms=min(stamps),end_ms=max(stamps),content_hash=hashlib.sha256(''.join(r.id for r in sorted(frames,key=lambda r:r.timestamp_ms)).encode()).hexdigest(),storage_key='legacy:'+mid))
        rt.media.add_frame(mid,ref.id,ref.timestamp_ms,raw)
        for ev in rt.db.list('event'):
            original=next((r for r in legacy_repository.list('candidate') if r.get('id')==ev['id']),None)
            if original and original['session_id']==ref.session_id and original['run_generation']==ref.run_generation:
                ev['media_ids']=[mid];rt.db.save('event',ev)
    for path in (rt.root/'data/procedures').glob('*'):
        if path.suffix not in ('.md','.txt') or path.name.lower()=='readme.md':continue
        text=path.read_text()[:12000];version=hashlib.sha256(text.encode()).hexdigest();id='procedure-'+version
        try:rt.db.get('source',id)
        except KeyError:rt.db.save('source',SourceExcerpt(id=id,source_id=id,title=path.name,section='full',text=text,source_type='procedure',version=version),immutable=True)
    marker={'id':'legacy-import-v1','count':count,'original_database':'data/artifacts/runs/lab.sqlite','status':'imported'}
    try:rt.db.get('migration',marker['id'])
    except KeyError:
        rt.audit.write('migration_finished',provenance=marker);rt.db.save('migration',marker)
    return marker
