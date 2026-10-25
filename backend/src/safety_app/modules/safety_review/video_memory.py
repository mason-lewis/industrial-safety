"""Derived FTS5 index of whole validated chunks, isolated by analysis ID."""
import re

from safety_app.domain.video_analysis import VideoChunk


class VideoMemory:
    def __init__(self, db, analysis_id):
        self.db = db
        self.analysis_id = analysis_id
        with db.connect() as conn:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS video_chunk_fts USING fts5(id UNINDEXED, analysis_id UNINDEXED, text, tokenize='porter unicode61')")
            fresh = conn.execute("SELECT 1 FROM sqlite_master WHERE name='video_chunk_insert'").fetchone() is None
            conn.execute("""CREATE TRIGGER IF NOT EXISTS video_chunk_insert AFTER INSERT ON objects
                WHEN new.kind='video_chunk' BEGIN
                INSERT INTO video_chunk_fts VALUES(new.id, json_extract(new.body,'$.analysis_id'),
                    json_extract(new.body,'$.summary') || ' ' || json_extract(new.body,'$.observations'));
                END""")
            if fresh:
                conn.execute("INSERT INTO video_chunk_fts SELECT id,json_extract(body,'$.analysis_id'),json_extract(body,'$.summary') || ' ' || json_extract(body,'$.observations') FROM objects WHERE kind='video_chunk'")

    def chunks(self):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT body FROM objects WHERE kind='video_chunk' AND json_extract(body,'$.analysis_id')=? ORDER BY rowid", (self.analysis_id,)).fetchall()
        return [VideoChunk.model_validate_json(row['body']) for row in rows]

    def catalog(self):
        return [{'id': chunk.id, 'intervals': [i.model_dump() for i in chunk.intervals],
                 'preview': chunk.summary[:300]} for chunk in self.chunks()]

    def search(self, query, limit=3):
        if not 1 <= limit <= 3 or len(query) > 500:
            raise ValueError('Memory search permits 1..3 chunks and at most 500 characters')
        terms = list(dict.fromkeys(re.findall(r'\w+', query.lower())))[:24]
        with self.db.connect() as conn:
            if terms:
                match = ' OR '.join('"' + term + '"' for term in terms)
                rows = conn.execute("""SELECT o.body FROM video_chunk_fts f JOIN objects o
                    ON o.kind='video_chunk' AND o.id=f.id WHERE video_chunk_fts MATCH ?
                    AND f.analysis_id=? ORDER BY bm25(video_chunk_fts),o.rowid DESC LIMIT ?""",
                    (match, self.analysis_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT body FROM objects WHERE kind='video_chunk' AND json_extract(body,'$.analysis_id')=? ORDER BY rowid DESC LIMIT ?", (self.analysis_id, limit)).fetchall()
        return [VideoChunk.model_validate_json(row['body']) for row in rows]
