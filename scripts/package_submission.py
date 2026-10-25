"""Copy only reviewed source/artifact paths. Never initialize Git in the data workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
FILES=['README.md','harness.md','AGENTS.md','.env.example','.gitignore','.python-version',
       'pyproject.toml','uv.lock','start','report.html','slides.html',
       'backend/pyproject.toml','frontend/package.json','frontend/package-lock.json',
       'frontend/index.html','frontend/tsconfig.json','frontend/vite.config.ts','submission/demo-video-script.md']
DIRECTORIES=['backend/app','backend/src','backend/tests','tests','frontend/src','frontend/public','scripts','configs','docs',
             'demo','submission/images','submission/vendor','submission/evidence','submission/pilot']
SUFFIXES={'.py','.md','.json','.yaml','.yml','.toml','.lock','.sh','.ts','.tsx','.css','.html','.txt','.ndjson',
          '.mp4','.png','.jpg','.jpeg','.svg','.js','.mjs','.woff','.woff2'}

def selected():
    result={ROOT/p for p in FILES if (ROOT/p).is_file()}
    for name in DIRECTORIES:
        directory=ROOT/name
        if not directory.exists():continue
        for path in directory.rglob('*'):
            if not path.is_file() or path.is_symlink():continue
            if name=='submission/images' and path.name.startswith(('slide-','report-page-')):continue
            if name=='docs' and path.suffix not in ('.md','.txt'):continue
            if any(p.startswith('.') or p in ('__pycache__','node_modules','dist') for p in path.relative_to(directory).parts):continue
            if path.suffix in SUFFIXES or path.name in ('uv','safety','download_youtube','LICENSE'):
                result.add(path)
    return sorted(result)

def package(destination):
    if destination.exists():raise ValueError('Destination must be new; existing work is never overwritten')
    destination.mkdir(parents=True)
    manifest=[]
    for source in selected():
        relative=source.relative_to(ROOT)
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        manifest.append({'path':str(relative),'bytes':source.stat().st_size,'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    (destination/'SUBMISSION_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return {'path':str(destination),'files':len(manifest),'bytes':sum(f['bytes'] for f in manifest),
            'excluded':'All developer data/, .env, databases, credentials, models, caches, dependency folders and Git history.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination',type=Path)
    print(json.dumps(package(parser.parse_args().destination.resolve()),indent=2))
