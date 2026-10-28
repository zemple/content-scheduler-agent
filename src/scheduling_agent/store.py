"""Transactional draft store. Pending suggestions do not reserve slots."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .domain import ValidationError, instant


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS drafts(
              request_id TEXT NOT NULL, material_id TEXT NOT NULL, draft_id TEXT UNIQUE NOT NULL,
              channel TEXT NOT NULL, time TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
              UNIQUE(request_id, material_id));
            CREATE UNIQUE INDEX IF NOT EXISTS reserved_slot ON drafts(channel, time)
              WHERE status='confirmed';
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, request):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT fingerprint FROM requests WHERE id=?', (request.request_id,)).fetchone()
            if row and row[0] != request.fingerprint():
                raise ValidationError('request_id already belongs to different input; use a new ID')
            db.execute('INSERT OR IGNORE INTO requests VALUES(?,?)', (request.request_id, request.fingerprint()))

    def drafts(self, request_id=None):
        with self.connect() as db:
            if request_id is None:
                rows = db.execute('SELECT payload FROM drafts ORDER BY request_id, material_id')
            else:
                rows = db.execute('SELECT payload FROM drafts WHERE request_id=? ORDER BY material_id', (request_id,))
            return [json.loads(row[0]) for row in rows]

    @staticmethod
    def occupied(db, channel, time, schedule, exclude=None):
        if any(s['channel'] == channel and instant(s['time']) == time for s in schedule):
            return True
        if exclude:
            # Pending proposals reserve within their own batch, but not across users/requests.
            rows = db.execute("SELECT request_id, material_id FROM drafts WHERE channel=? AND time=? AND (status='confirmed' OR request_id=?)", (channel, time, exclude[0]))
        else:
            rows = db.execute("SELECT request_id, material_id FROM drafts WHERE channel=? AND time=? AND status='confirmed'", (channel, time))
        return any(tuple(row) != exclude for row in rows)

    def available(self, channel, time, schedule, exclude=None):
        with self.connect() as db:
            return not self.occupied(db, channel, instant(time), schedule, exclude)

    def save(self, request, item, material, time, pending, schedule):
        time = instant(time)
        key = (request.request_id, item['material_id'])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload FROM drafts WHERE request_id=? AND material_id=?', key).fetchone()
            if old:
                draft = json.loads(old[0])
                if draft['suggested_time'] != time:
                    return {'ok': False, 'error': 'draft_immutable', 'message': 'Use a new request_id to change a saved draft.'}
                if self.occupied(db, material['channel'], time, schedule, exclude=key):
                    return {'ok': False, 'error': 'saved_slot_now_conflicts', 'message': 'Historical draft retained; current slot requires revalidation.'}
                if draft['channel'] != material['channel']:
                    return {'ok': False, 'error': 'saved_channel_changed', 'message': 'Use a new request_id for changed material channel.'}
                if set(pending) - set(draft['pending_items']):
                    return {'ok': False, 'error': 'saved_requires_revalidation', 'message': 'New risks require review of the historical draft.'}
                return {'ok': True, 'duplicate': True, 'draft': draft}
            if self.occupied(db, material['channel'], time, schedule, exclude=key):
                return {'ok': False, 'error': 'slot_conflict', 'message': 'Slot became occupied; query another slot.'}
            status = 'pending' if pending else 'confirmed'
            draft = {'draft_id': 'D-' + uuid.uuid4().hex[:16], 'request_id': key[0],
                     'material_id': key[1], 'requested_time': item['requested_time'],
                     'suggested_time': time, 'channel': material['channel'],
                     'confirmation_status': status, 'pending_items': pending,
                     'publication_status': 'not_published'}
            db.execute('INSERT INTO drafts VALUES(?,?,?,?,?,?,?)',
                       (*key, draft['draft_id'], material['channel'], time, status,
                        json.dumps(draft, ensure_ascii=False)))
            return {'ok': True, 'duplicate': False, 'draft': draft}
