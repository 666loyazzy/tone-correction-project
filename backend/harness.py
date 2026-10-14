from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from .database import Practice, SessionLocal

SKILLS_DIR = Path(__file__).resolve().parents[1] / 'skills'
MANIFEST = json.loads((SKILLS_DIR / 'manifest.json').read_text(encoding='utf-8'))
STEPS = MANIFEST['analysis_steps']


def skill_text(name: str) -> str:
    allowed = {entry['skill'] for entry in STEPS}
    if name not in allowed:
        raise ValueError('Unknown skill')
    return (SKILLS_DIR / name / 'SKILL.md').read_text(encoding='utf-8')


def initial_trace():
    return [{**entry, 'status':'pending', 'skill_version':
             hashlib.sha256(skill_text(entry['skill']).encode()).hexdigest()[:12]} for entry in STEPS]


class AnalysisHarness:
    """Code-enforced ordered stages. The language model cannot reorder these."""
    def __init__(self, practice_id: str):
        self.practice_id = practice_id
        self.trace = initial_trace()
        self.position = 0
        self.active = None

    def _save(self):
        with SessionLocal() as db:
            practice = db.get(Practice, self.practice_id)
            if practice:
                practice.trace = [dict(entry) for entry in self.trace]
                practice.updated_at = time.time()
                db.commit()

    def begin(self, stage: str):
        if self.active is not None or self.position >= len(STEPS) or STEPS[self.position]['id'] != stage:
            raise RuntimeError('Harness stage order violation')
        self.active = self.position
        self.trace[self.position].update(status='running', started_at=time.time())
        self._save()

    def end(self, summary: str):
        if self.active is None:
            raise RuntimeError('No active harness stage')
        entry = self.trace[self.active]
        entry.update(status='completed', finished_at=time.time(), summary=summary)
        self.position += 1
        self.active = None
        self._save()

    def fail(self, reason: str):
        if self.active is not None:
            self.trace[self.active].update(status='failed', finished_at=time.time(), summary=reason)
            self.active = None
        self._save()

    def complete(self):
        if self.position != len(STEPS) or self.active is not None:
            raise RuntimeError('Incomplete harness')


def validate_chart(result: dict):
    times, values = result['time_s'], result['f0_hz']
    if len(times) != len(values) or not all(math.isfinite(t) for t in times):
        raise ValueError('Invalid chart data')
    if any(second <= first for first, second in zip(times, times[1:])):
        raise ValueError('Non-monotonic timestamps')
    if any(value is not None and (not math.isfinite(value) or value <= 0) for value in values):
        raise ValueError('Invalid F0 value')
