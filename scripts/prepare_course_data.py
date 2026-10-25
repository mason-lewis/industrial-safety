"""Make attributed, neutral-name demo derivatives; originals are never modified."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src'),str(ROOT/'scripts')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset',type=Path)
    args=parser.parse_args()
    import imageio_ffmpeg
    from import_safety_library import records
    out=ROOT/'demo';(out/'videos').mkdir(parents=True,exist_ok=True)
    clips=[];provenance=[]
    for folder in sorted((args.dataset/'test').iterdir()):
        if not folder.is_dir():continue
        candidates=sorted(folder.glob('*.mp4'),key=lambda p:(p.stat().st_size,p.name))[:3]
        for source in candidates:
            id=f'clip-{len(clips)+1:03d}';target=out/'videos'/f'{id}.mp4'
            if not target.exists():
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error',
                    '-i',str(source),'-map','0:v:0','-an','-vf','scale=1280:-2','-c:v','libx264','-crf','25',
                    '-preset','fast','-movflags','+faststart','-n',str(target)],check=True)
            digest=hashlib.sha256(target.read_bytes()).hexdigest()
            clips.append({'id':id,'file':str(target.relative_to(out)),'sha256':digest,
                          'original_split':'test','source_group':'unresolved-recording-group',
                          'independent_label':None,'app_demo':len(clips)%3==0})
            provenance.append({'id':id,'original_file':str(source.relative_to(args.dataset)),
                'original_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'source_behavior_label':folder.name,'modifications':'Full clip, resized to 1280px wide, H.264 CRF25, audio removed. No event edits.'})
    manifest={'dataset':'Video Dataset for Safe and Unsafe Behaviours','authors':'Önal and Dandıl',
        'year':2024,'version':1,'doi':'10.17632/xjmtb22pff.1','url':'https://data.mendeley.com/datasets/xjmtb22pff/1',
        'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
        'location':'Eskişehir, Türkiye','independent_labels_complete':False,
        'limitations':['Original behavior labels are not visible-hazard ground truth.',
            'Raw recording/session identity is unavailable; independence between clips is unverified.',
            'No US jurisdiction or recording calendar time is established.'], 'clips':clips}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    selected={'cfr-1910-5','cfr-1910-12','cfr-1910-22','cfr-1910-36','cfr-1910-37','cfr-1910-132','cfr-1910-176'}
    rows=[r for r in records(ROOT/'data/resources/safety/snapshots/federal-2026-09-30','local') if r.source_id in selected]
    (out/'safety-sources.ndjson').write_text(''.join(r.model_dump_json()+'\n' for r in rows))
    print(json.dumps({'clips':len(clips),'video_bytes':sum((out/c['file']).stat().st_size for c in clips),
                      'source_blocks':len(rows),'independent_labels_complete':False},indent=2))


if __name__=='__main__':main()
