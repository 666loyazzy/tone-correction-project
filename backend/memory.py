import time
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .database import LearningMemory, SessionLocal


def load_memory(user_id: str):
    with SessionLocal() as db:
        memory = db.get(LearningMemory, user_id)
        if memory is None:
            memory = LearningMemory(user_id=user_id)
            db.add(memory)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                memory = db.get(LearningMemory, user_id)
        return {'enabled': bool(memory.enabled), 'goal': memory.goal,
                'facts': memory.facts if memory.enabled else [], 'updated_at': memory.updated_at}


def remember_practice(user_id: str, practice_id: str, result: dict):
    load_memory(user_id)
    with SessionLocal() as db:
        memory = db.scalar(select(LearningMemory).where(LearningMemory.user_id==user_id).with_for_update())
        if not memory.enabled:
            return '长期记忆已关闭，未写入摘要'
        fact = {'practice_id':practice_id, 'created_at':time.time(), 'tone':result['tone'],
                'features':result['features'], 'quality_warnings':result['quality']['warnings'],
                'algorithm':result['algorithm'], 'version':'1.0.0'}
        memory.facts = [fact] + [entry for entry in memory.facts if entry['practice_id'] != practice_id][:19]
        memory.updated_at = time.time()
        db.commit()
    return '已保存本次观测摘要；未推断声调能力或医学属性'
