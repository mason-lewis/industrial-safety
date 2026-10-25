"""Relocation preserves bytes and refuses ambiguous or active stores."""
import json
import sqlite3
from pathlib import Path

import pytest

from scripts import consolidate_data


def test_move_preserves_records_files_and_existing_input(tmp_path, monkeypatch):
    store = tmp_path / 'artifacts' / 'runs'
    store.mkdir(parents=True)
    with sqlite3.connect(store / 'lab.sqlite') as db:
        db.execute('CREATE TABLE records(id TEXT, body TEXT)')
        db.execute('INSERT INTO records VALUES (?, ?)', ('historical', 'original record'))
    (tmp_path / 'artifacts' / 'weights.pth').write_bytes(b'unchanged checkpoint')
    source = tmp_path / 'data' / 'input' / 'source.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original footage')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config' / 'lab.json').write_text('{"version": 26}')
    before = consolidate_data.hashes(tmp_path / 'artifacts')
    checks = []
    monkeypatch.setattr(consolidate_data, 'assert_quiescent', lambda root: checks.append(root))
    result = consolidate_data.consolidate(tmp_path)
    assert len(checks) == 2
    assert result['status'] == 'completed'
    assert consolidate_data.hashes(tmp_path / 'data' / 'artifacts') == before
    with sqlite3.connect(tmp_path / 'data' / 'artifacts' / 'runs' / 'lab.sqlite') as db:
        assert db.execute('SELECT * FROM records').fetchall() == [('historical', 'original record')]
    assert source.read_bytes() == b'original footage'
    assert (tmp_path / 'data' / 'config' / 'lab.json').read_text() == '{"version": 26}'
    journal = json.loads(Path(result['journal']).read_text())
    assert journal['status'] == 'completed'
    assert all(move['status'] == 'verified' for move in journal['moves'])
    assert consolidate_data.consolidate(tmp_path)['status'] == 'already_consolidated'


def test_collision_prevents_all_moves(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'config').mkdir()
    (tmp_path / 'data' / 'config').mkdir(parents=True)
    with pytest.raises(ValueError, match='Destination already exists'):
        consolidate_data.consolidate(tmp_path)
    assert (tmp_path / 'artifacts').is_dir()
    assert not (tmp_path / 'data' / 'artifacts').exists()


def test_open_database_prevents_move(tmp_path, monkeypatch):
    (tmp_path / 'artifacts').mkdir()

    def active(root):
        raise RuntimeError('Stop the API')

    monkeypatch.setattr(consolidate_data, 'assert_quiescent', active)
    with pytest.raises(RuntimeError, match='Stop the API'):
        consolidate_data.consolidate(tmp_path)
    assert (tmp_path / 'artifacts').is_dir()
    assert not (tmp_path / 'data').exists()


def test_symlink_prevents_move(tmp_path):
    (tmp_path / 'artifacts').mkdir()
    (tmp_path / 'artifacts' / 'external').symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='Symlink requires manual review'):
        consolidate_data.plan(tmp_path)
