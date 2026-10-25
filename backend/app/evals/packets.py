"""Export frozen live packets for A/B without decoding or repeating CV inference."""
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
from io import BytesIO
from app.evidence.store import EvidenceStore


def export_cache(store,repository,session,model_config):
    filters={'session_id':session['session_id'],'run_generation':session['run_generation']}
    packets=repository.list('scene_packet',filters=filters)
    packets=[p for p in packets if p['trigger'] in ('scheduled','cv_trigger')]
    unique={}
    for packet in packets: unique[(packet['cutoff_ms'],packet['trigger'])]=packet
    packets=sorted(unique.values(),key=lambda p:(p['cutoff_ms'],p['trigger']))
    if not packets: raise ValueError('No saved packets in this playback generation. Enable AI and play footage first.')
    refs={r['id']:r for p in packets for r in [*p['refs'],*p.get('extra_refs',[])]}
    output=BytesIO()
    with tempfile.TemporaryDirectory(prefix='scene-cache-') as folder:
        root=Path(folder);cached=EvidenceStore(root/'evidence',max_bytes=512*1024*1024);hashes={}
        from app.models.contracts import EvidenceRef
        for id,value in refs.items():
            ref=EvidenceRef.model_validate(value)
            try: raw=store.image(id)
            except KeyError: raise ValueError('Packet evidence expired; select a shorter current run')
            if store.records[id][0]!=ref: raise ValueError('Evidence metadata mismatch')
            cached.records[id]=(ref,len(raw));cached.packets[id]=store.packets[id];cached.pins.add(id)
            (cached.root/(id+'.jpg')).write_bytes(raw);cached._persist(id)
            hashes[id]=hashlib.sha256(raw).hexdigest()
        cached.db.close()
        start=min(p['start_ms'] for p in packets);end=max(p['cutoff_ms'] for p in packets)
        manifest={'version':1,'packets':packets,'images_sha256':hashes,'model':model_config.resolve('safety_coordinator'),
                  'reasoning_effort':model_config.reasoning_effort,'schema':'SceneResult','labels':[],
                  'coverage':[{'duration_ms':end-start}],'duration_ms':end-start,
                  'extra_policy':'Frozen extra_refs and crops only; identical available images in every arm',
                  'limitations':['Export of source-time live windows; CV metadata is frozen exactly as available at each cutoff. Human labels required.']}
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in root.rglob('*'):
                if path.is_file(): archive.write(path,str(path.relative_to(root)))
    return output.getvalue()
