"""Exact, permission/context/version-bound cache. Never stores final safety answers."""
import hashlib
import json
import time


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class ExactCache:
    def __init__(self, db, scope, clock=time.time):
        self.db, self.scope, self.clock = db, scope, clock
        with db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS chat_exact_cache(key TEXT PRIMARY KEY, category TEXT, expires REAL, body TEXT)')

    def key(self, category, arguments):
        if category not in ('classification', 'retrieval', 'web_discovery', 'token_count'):
            raise ValueError('This cache cannot store final answers')
        return fingerprint({'scope': self.scope, 'category': category, 'arguments': arguments})

    def get(self, category, arguments):
        with self.db.connect() as conn:
            row = conn.execute('SELECT body FROM chat_exact_cache WHERE key=? AND expires>?', (self.key(category, arguments), self.clock())).fetchone()
        return json.loads(row['body']) if row else None

    def put(self, category, arguments, value, ttl):
        with self.db.connect() as conn:
            conn.execute('INSERT INTO chat_exact_cache VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET expires=excluded.expires,body=excluded.body',
                         (self.key(category, arguments), category, self.clock()+ttl, json.dumps(value)))
