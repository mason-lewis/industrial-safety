"""Preview or move legacy data folders under data, preserving every file byte."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

FOLDERS = ('artifacts', 'evals', 'resources', 'config')


def plan(root):
    root = Path(root).resolve()
    moves = []
    for name in FOLDERS:
        source = root / name
        destination = root / 'data' / name
        if not source.exists():
            continue
        if destination.exists() or destination.is_symlink():
            raise ValueError(f'Destination already exists: data/{name}; resolve it before moving')
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f'Expected an ordinary directory: {name}')
        files = []
        for base, directories, names in os.walk(source):
            for entry in directories + names:
                path = Path(base) / entry
                if path.is_symlink():
                    raise ValueError(f'Symlink requires manual review: {path.relative_to(root)}')
            files.extend(Path(base) / entry for entry in names)
        moves.append({'source': name, 'destination': f'data/{name}',
                      'files': len(files), 'bytes': sum(path.stat().st_size for path in files)})
    return moves


def assert_quiescent(root):
    """Open database handles mean the API, workers, or another reader must stop."""
    executable = shutil.which('lsof')
    if not executable:
        raise RuntimeError('lsof is required to verify that databases are closed before moving')
    databases = [path for folder in (*FOLDERS, 'data')
                 for path in (root / folder).rglob('*.sqlite') if path.is_file()]
    if not databases:
        return
    result = subprocess.run([executable, '-t', '--', *map(str, databases)],
                            capture_output=True, text=True, check=False)
    if result.stdout.strip():
        raise RuntimeError('Stop the API, workers and database readers before consolidating data')
    if result.returncode not in (0, 1):
        raise RuntimeError('Could not verify database handles; no files moved')


def hashes(folder):
    values = {}
    for path in sorted(folder.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                values[str(path.relative_to(folder))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return values


def save_journal(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2) + '\n')
    temporary.replace(path)


def consolidate(root):
    root = Path(root).resolve()
    moves = plan(root)
    if not moves:
        return {'status': 'already_consolidated', 'moves': []}
    assert_quiescent(root)
    for move in moves:
        move['sha256'] = hashes(root / move['source'])
        move['status'] = 'pending'
    # Check again after hashing so a newly opened database cannot go unnoticed.
    assert_quiescent(root)
    journal = root / 'data' / 'migrations' / f'consolidate-{uuid4().hex}.json'
    journal.parent.mkdir(parents=True, exist_ok=True)
    record = {'status': 'in_progress', 'moves': moves}
    save_journal(journal, record)
    for move in moves:
        source = root / move['source']
        destination = root / move['destination']
        if destination.exists():
            raise ValueError(f'Destination appeared during migration: {move["destination"]}')
        source.rename(destination)
        if hashes(destination) != move['sha256']:
            raise RuntimeError(f'File verification failed: {move["destination"]}; files and journal retained')
        move['status'] = 'verified'
        save_journal(journal, record)
    record['status'] = 'completed'
    save_journal(journal, record)
    return {'status': 'completed', 'journal': str(journal),
            'moves': [{key: value for key, value in move.items() if key != 'sha256'} for move in moves]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Move after collision and open-database checks')
    args = parser.parse_args()
    result = consolidate(args.root) if args.apply else {'status': 'preview', 'moves': plan(args.root)}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
