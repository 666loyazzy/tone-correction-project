from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from celery import Celery
from sqlalchemy import delete, select

from .acoustics import AudioError, analyze, decode_audio
from .database import DATA_DIR, GuestQuota, Practice, SessionLocal, User
from .auth import GUEST_SECONDS
from .harness import AnalysisHarness, validate_chart
from .memory import remember_practice

logger = logging.getLogger(__name__)
celery_app = Celery('tone_correction', broker=os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
celery_app.conf.update(task_serializer='json', accept_content=['json'],
                       task_ignore_result=True, broker_connection_retry_on_startup=True,
                       task_acks_late=True, task_reject_on_worker_lost=True,
                       worker_prefetch_multiplier=1,
                       beat_schedule={'audio-retention': {'task': 'tone_correction.cleanup', 'schedule': 3600}})


def practice_directory(identifier: str) -> Path:
    return DATA_DIR / 'practices' / identifier


def run_analysis(identifier: str):
    with SessionLocal() as db:
        practice = db.get(Practice, identifier)
        if not practice or practice.state == 'completed':
            return
        parameters = dict(practice.parameters)
        tone = practice.tone
        user_id = practice.user_id
        practice.state = 'running'
        practice.updated_at = time.time()
        db.commit()
    directory = practice_directory(identifier)
    harness = AnalysisHarness(identifier)
    try:
        harness.begin('audio.decode')
        samples, rate = decode_audio(directory / 'source.audio', directory / 'audio.wav')
        harness.end(f'已解码单声道音频，{rate} Hz，{len(samples)/rate:.2f} 秒')
        result = analyze(samples, rate, tone, parameters['floor'], parameters['ceiling'],
                         parameters['start'], parameters['end'], harness=harness,reference=parameters.get('reference'))
        harness.begin('chart.prepare')
        validate_chart(result)
        harness.end('时间与 F0 数组通过校验；振幅波形与 Hz 曲线分开')
        harness.begin('memory.update')
        harness.end(remember_practice(user_id, identifier, result))
        harness.complete()
        with SessionLocal() as db:
            practice = db.get(Practice, identifier)
            if practice:
                practice.result = result
                practice.state = 'completed'
                practice.error = None
                practice.updated_at = time.time()
                db.commit()
    except Exception as exc:
        error = str(exc) if isinstance(exc, AudioError) else '分析暂时未完成，请重新提交或联系教师。'
        harness.fail(error)
        if not isinstance(exc, AudioError):
            logger.exception('Analysis failed for practice %s', identifier)
        with SessionLocal() as db:
            practice = db.get(Practice, identifier)
            if practice:
                practice.state = 'failed'
                practice.error = error
                practice.updated_at = time.time()
                db.commit()
        shutil.rmtree(directory, ignore_errors=True)


@celery_app.task(name='tone_correction.analyze')
def queued_analysis(identifier: str):
    run_analysis(identifier)


@celery_app.task(name='tone_correction.cleanup')
def cleanup():
    cutoff = time.time() - max(1, int(os.getenv('AUDIO_RETENTION_DAYS', '30'))) * 86400
    with SessionLocal() as db:
        expired = db.scalars(select(Practice).where(Practice.created_at < cutoff,
                          Practice.state.in_(['completed', 'failed']))).all()
        for practice in expired:
            shutil.rmtree(practice_directory(practice.id), ignore_errors=True)
        guest_ids = list(db.scalars(select(User.id).where(User.role == 'guest', User.created_at < time.time() - GUEST_SECONDS)))
        guest_practices = list(db.scalars(select(Practice.id).where(Practice.user_id.in_(guest_ids)))) if guest_ids else []
        if guest_ids:
            db.execute(delete(User).where(User.id.in_(guest_ids)))
        db.execute(delete(GuestQuota).where(GuestQuota.expires_at < time.time()))
        db.commit()
    for practice_id in guest_practices:
        shutil.rmtree(practice_directory(practice_id), ignore_errors=True)
