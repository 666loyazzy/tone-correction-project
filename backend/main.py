from __future__ import annotations

import asyncio
import csv
import io
import math
import os
import shutil
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
import json
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func

from .auth import current_user, public_user, router as auth_router, teacher
from .database import (Conversation, LearningMemory, Message, Practice, SessionLocal, User,
                       engine, identifier, initialize)
from .harness import MANIFEST, initial_trace, AnalysisHarness, load_chat_skills
from .jobs import cleanup, practice_directory, queued_analysis, run_analysis
from .memory import load_memory
from .tutor import answer, retrieve, validate_answer
from .scientific import evidence
from .scoring import ensure_score
from .progress import router as progress_router, selected_reference
from .resynthesis import resynthesize
from .acoustics import AudioError
from .exercises import validate_syllable, practice_syllable

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 12 * 1024 * 1024
QUEUE_MODE = os.getenv('QUEUE_MODE', 'local')


async def local_maintenance():
    while True:
        await asyncio.to_thread(cleanup)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    initialize()
    task = asyncio.create_task(local_maintenance()) if QUEUE_MODE == 'local' else None
    if QUEUE_MODE == 'local':
        with SessionLocal() as db:
            pending = db.scalars(select(Practice.id).where(Practice.state.in_(['queued', 'running']))).all()
        for practice_id in pending:
            await asyncio.to_thread(run_analysis, practice_id)
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    engine.dispose()


app = FastAPI(title='智耳正音', version='1.0.0', lifespan=lifespan)
app.include_router(auth_router)
app.include_router(progress_router)


@app.middleware('http')
async def browser_headers(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and origin:
        if urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail':'请从同一网站提交请求。'}, status_code=403)
    response = await call_next(request)
    response.headers['Permissions-Policy'] = 'microphone=(self), camera=()'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
    return response


@app.get('/api/health')
def health():
    with SessionLocal() as db:
        db.execute(select(1))
    return {'status':'ok', 'version':'1.0.0', 'queue_mode':QUEUE_MODE,
            'tutor_api_configured':bool(os.getenv('LLM_API_KEY') and os.getenv('LLM_MODEL'))}


def owned_practice(db, practice_id: str, user: User, allow_teacher=False):
    practice = db.get(Practice, practice_id)
    permitted = practice and practice.user_id == user.id
    if not permitted and allow_teacher and user.role == 'teacher' and practice:
        student = db.get(User, practice.user_id)
        permitted = student and student.teacher_id == user.id
    if not permitted:
        raise HTTPException(404, '没有找到这条练习。')
    return practice


def serialize_practice(practice):
    return {'id':practice.id, 'tone':practice.tone, 'syllable':practice_syllable(practice), 'filename':practice.filename,
            'created_at':practice.created_at, 'state':practice.state, 'result':ensure_score(practice.result),
            'error':practice.error, 'trace':practice.trace,
            'audio_available':(practice_directory(practice.id)/'audio.wav').exists()}


@app.post('/api/analyses', status_code=202)
async def create_analysis(background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    tone: Annotated[int, Form(ge=1, le=4)] = 3,
    syllable: Annotated[str, Form(max_length=10)] = 'ma',
    pitch_floor: Annotated[float, Form()] = 75,
    pitch_ceiling: Annotated[float, Form()] = 600,
    start_s: Annotated[float | None, Form()] = None,
    end_s: Annotated[float | None, Form()] = None,
    reference_id: Annotated[str | None, Form()] = None,
    user: User = Depends(current_user)):
    practice_id = identifier()
    directory = practice_directory(practice_id)
    directory.mkdir(parents=True, mode=0o700)
    queued = False
    try:
        validate_syllable(syllable)
        if not (math.isfinite(pitch_floor) and math.isfinite(pitch_ceiling) and 50 <= pitch_floor < pitch_ceiling <= 1000):
            raise HTTPException(400, '基频范围需要满足 50 ≤ 下限 < 上限 ≤ 1000 Hz。')
        if (start_s is None) != (end_s is None) or (start_s is not None and (
                not math.isfinite(start_s) or not math.isfinite(end_s) or start_s < 0 or end_s-start_s < 0.12)):
            raise HTTPException(400, '请设置有效选区，至少包含 0.12 秒。')
        size = 0
        source = directory/'source.audio'
        with source.open('wb') as output:
            while chunk := await file.read(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, '文件超过 12 MB，请上传较短的录音。')
                output.write(chunk)
        source.chmod(0o600)
        if not size:
            raise HTTPException(400, '录音文件为空，请重新录音。')
        with SessionLocal() as db:
            active_count = len(db.scalars(select(Practice.id).where(Practice.user_id == user.id,
                        Practice.state.in_(['queued','running']))).all())
            if active_count >= 3:
                raise HTTPException(429, '已有练习正在分析，请稍候。')
            db.add(Practice(id=practice_id, user_id=user.id, tone=tone,
                filename=Path(file.filename or 'recording').name[:120], parameters={
                    'floor':pitch_floor, 'ceiling':pitch_ceiling, 'start':start_s, 'end':end_s,
                    'syllable':syllable, 'reference':selected_reference(user,tone,reference_id,syllable)},
                trace=initial_trace()))
            db.commit()
        if QUEUE_MODE == 'celery':
            try:
                queued_analysis.apply_async(args=[practice_id], retry=False)
            except Exception:
                with SessionLocal() as db:
                    practice = db.get(Practice, practice_id)
                    practice.state='failed'; practice.error='任务队列暂时不可用，请稍后重试。'; db.commit()
                raise HTTPException(503, '任务队列暂时不可用，录音仍在页面，可稍后重试。') from None
        else:
            background.add_task(run_analysis, practice_id)
        queued = True
        return {'id':practice_id, 'state':'queued'}
    finally:
        await file.close()
        if not queued:
            shutil.rmtree(directory, ignore_errors=True)


@app.get('/api/practices')
def practices(limit: int = 50, user: User = Depends(current_user)):
    with SessionLocal() as db:
        rows=db.scalars(select(Practice).where(Practice.user_id==user.id)
                        .order_by(Practice.created_at.desc()).limit(max(1,min(limit,100)))).all()
        return {'practices':[serialize_practice(row) for row in rows]}


@app.get('/api/practices/{practice_id}')
def practice(practice_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        return serialize_practice(owned_practice(db,practice_id,user,allow_teacher=True))


@app.get('/api/practices/{practice_id}/audio')
def audio(practice_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        owned_practice(db,practice_id,user,allow_teacher=True)
    path=practice_directory(practice_id)/'audio.wav'
    if not path.exists():
        raise HTTPException(404, '录音已过保留期限或尚未完成分析。')
    return FileResponse(path,media_type='audio/wav')


@app.get('/api/practices/{practice_id}/export')
def export(practice_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        result=owned_practice(db,practice_id,user,allow_teacher=True).result
    if not result:
        raise HTTPException(409,'这条练习还没有分析结果。')
    output=io.StringIO(); writer=csv.writer(output); writer.writerow(['Time_s','F0_Hz'])
    writer.writerows(zip(result['time_s'],result['f0_hz']))
    return Response('\ufeff'+output.getvalue(),media_type='text/csv;charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="f0-{practice_id}.csv"'})


@app.get('/api/practices/{practice_id}/corrected-audio')
async def corrected_audio(practice_id: str,user: User=Depends(current_user)):
    with SessionLocal() as db:
        result=ensure_score(owned_practice(db,practice_id,user,allow_teacher=True).result)
    if not result:raise HTTPException(409,'请先完成声学分析。')
    folder=practice_directory(practice_id);source=folder/'audio.wav'
    if not source.exists():raise HTTPException(404,'原录音已过保留期，请重新录音。')
    destination=folder/('corrected-'+result['score']['comparison_key']+'.wav')
    if not destination.exists():
        try:await asyncio.to_thread(resynthesize,source,destination,result)
        except (AudioError,RuntimeError) as exc:
            raise HTTPException(400,str(exc) if isinstance(exc,AudioError) else '纠正示范生成未完成，请换一段清晰的完整音节。') from None
    return FileResponse(destination,media_type='audio/wav')


@app.delete('/api/practices/{practice_id}')
def remove_practice(practice_id: str, user: User = Depends(current_user)):
    with SessionLocal() as db:
        row=owned_practice(db,practice_id,user)
        if row.state in ('queued','running'):
            raise HTTPException(409,'分析进行中，完成后可以删除。')
        memory=db.get(LearningMemory,user.id)
        if memory:
            memory.facts=[entry for entry in memory.facts if entry['practice_id']!=practice_id]
        db.delete(row); db.commit()
    shutil.rmtree(practice_directory(practice_id),ignore_errors=True)
    return {'ok':True}


@app.get('/api/agent/config')
def agent_config(user: User = Depends(current_user)):
    return {'skills':MANIFEST['analysis_steps'],'chat_steps':MANIFEST['chat_steps'],
            'version':MANIFEST['version'],'model_configured':bool(os.getenv('LLM_API_KEY') and os.getenv('LLM_MODEL'))}


@app.get('/api/memory')
def memory(user: User = Depends(current_user)):
    return load_memory(user.id)


class MemoryUpdate(BaseModel):
    enabled: bool = True
    goal: str = Field(default='',max_length=500)


@app.put('/api/memory')
def update_memory(body: MemoryUpdate,user: User = Depends(current_user)):
    load_memory(user.id)
    with SessionLocal() as db:
        memory=db.get(LearningMemory,user.id)
        memory.enabled=int(body.enabled); memory.goal=body.goal; memory.updated_at=time.time(); db.commit()
    return load_memory(user.id)


@app.delete('/api/memory')
def clear_memory(user: User = Depends(current_user)):
    load_memory(user.id)
    with SessionLocal() as db:
        memory=db.get(LearningMemory,user.id); memory.facts=[]; memory.updated_at=time.time(); db.commit()
    return {'ok':True}


@app.get('/api/conversations')
def conversations(user: User = Depends(current_user)):
    with SessionLocal() as db:
        rows=db.scalars(select(Conversation).where(Conversation.user_id==user.id)
                        .order_by(Conversation.updated_at.desc()).limit(30)).all()
        return {'conversations':[{'id':row.id,'title':row.title} for row in rows]}


def owned_conversation(db,conversation_id,user):
    row=db.get(Conversation,conversation_id)
    if not row or row.user_id!=user.id:
        raise HTTPException(404,'没有找到这段对话。')
    return row


@app.get('/api/conversations/{conversation_id}')
def conversation(conversation_id: str,user: User=Depends(current_user)):
    with SessionLocal() as db:
        row=owned_conversation(db,conversation_id,user)
        messages=db.scalars(select(Message).where(Message.conversation_id==row.id)
                            .order_by(Message.created_at).limit(100)).all()
        return {'id':row.id,'title':row.title,'messages':[{'role':m.role,'content':m.content,'sources':m.sources} for m in messages]}


@app.delete('/api/conversations/{conversation_id}')
def remove_conversation(conversation_id: str,user: User=Depends(current_user)):
    with SessionLocal() as db:
        row=owned_conversation(db,conversation_id,user)
        db.execute(delete(Message).where(Message.conversation_id==row.id));db.delete(row);db.commit()
    return {'ok':True}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1,max_length=2000)
    tone: int = Field(default=3,ge=1,le=4)
    syllable: str = Field(default='ma',max_length=10)
    conversation_id: str | None = None
    analysis_id: str | None = None


async def run_chat(body: ChatRequest,user: User,on_trace=None):
    validate_syllable(body.syllable)
    harness=AnalysisHarness(None, MANIFEST['chat_workflow'],on_trace=on_trace)
    harness.begin('chat.authorize')
    with SessionLocal() as db:
        row=owned_conversation(db,body.conversation_id,user) if body.conversation_id else Conversation(
            user_id=user.id,title=body.message[:40])
        if not body.conversation_id:
            db.add(row); db.commit()
        practice=owned_practice(db,body.analysis_id,user) if body.analysis_id else None
        if practice and (practice.state != 'completed' or not practice.result):
            raise HTTPException(409,'请等待这次练习完成声学分析。')
        result=ensure_score(practice.result) if practice else None
        target_tone=practice.tone if practice else body.tone
        target_syllable=practice_syllable(practice) if practice else body.syllable
        past=db.scalars(select(Message).where(Message.conversation_id==row.id)
                        .order_by(Message.created_at.desc()).limit(10)).all()
        context=[{'role':m.role,'content':m.content} for m in reversed(past)]
        conversation_id=row.id
        previous=None
        if practice:
            prior=db.scalar(select(Practice).where(Practice.user_id==user.id, Practice.tone==practice.tone,
                func.coalesce(Practice.parameters['syllable'].as_string(),'ma')==target_syllable,
                Practice.state=='completed', Practice.created_at<practice.created_at)
                .order_by(Practice.created_at.desc()).limit(1))
            if prior and prior.result:
                previous={'id':prior.id, 'created_at':prior.created_at, 'result':ensure_score(prior.result)}
    harness.end('会话及练习由服务器校验权限；声调以保存的练习为准')
    harness.begin('chat.evidence')
    memory=load_memory(user.id)
    measured=evidence(result,previous)
    if measured:
        measured['practice_id']=body.analysis_id
    harness.end('已计算前中后段观测、有效帧比例及同声调复练变化' if measured else '当前没有分析数据，进入录音准备指导')
    harness.begin('chat.retrieve')
    entries=retrieve(body.message,target_tone)
    skills,versions=load_chat_skills()
    harness.trace[harness.active]['loaded_skills']=versions
    harness.end('强制读取 '+', '.join(item['name'] for item in versions)+'；资料：'+', '.join(item['id'] for item in entries))
    harness.begin('chat.answer')
    try:
        response=await answer(body.message,target_tone,result,context,memory,measured=measured,entries=entries,skills=skills,syllable=target_syllable)
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError) as exc:
        reason=(f'模型 API 返回 HTTP {exc.response.status_code}，请检查密钥、额度与模型配置。' if isinstance(exc,httpx.HTTPStatusError)
                else '模型回复被截断，请增加输出预算。' if isinstance(exc,ValueError) and '被截断' in str(exc)
                else '模型请求未完成，请检查网络、模型配置或超时时间；已提取的声学结果仍可查看。')
        harness.fail(reason)
        raise HTTPException(503,{'message':reason,'trace':harness.trace}) from None
    harness.end('模型已返回发音分析与练习建议' if response['mode']=='api' else '模型尚未配置，已根据实测曲线生成练习建议')
    harness.begin('chat.validate')
    response=validate_answer(response,measured)
    harness.end('已执行 anti-defensive-writing；无依据的测量数值或教学结论已替换为实测练习建议' if response['mode']=='guarded' else '已执行 anti-defensive-writing 表述检查、测量数值与教学边界校验')
    harness.begin('chat.save')
    with SessionLocal() as db:
        row=owned_conversation(db,conversation_id,user)
        row.updated_at=time.time()
        db.add(Message(conversation_id=row.id,role='user',content=body.message))
        db.add(Message(conversation_id=row.id,role='assistant',content=response['text'],sources=response['sources']))
        db.commit()
    harness.end('已保存分析、练习建议和资料，可在历史对话中继续追问')
    harness.complete()
    return {**response,'conversation_id':conversation_id,'trace':harness.trace,'evidence':measured}


@app.post('/api/chat')
async def chat(body: ChatRequest,user: User=Depends(current_user)):
    return await run_chat(body,user)


@app.post('/api/chat/stream')
async def chat_stream(body: ChatRequest,user: User=Depends(current_user)):
    queue=asyncio.Queue()
    async def produce():
        try:
            data=await run_chat(body,user,lambda trace:queue.put_nowait({'type':'trace','trace':trace}))
            queue.put_nowait({'type':'result','data':data})
        except HTTPException as exc:
            detail=exc.detail
            queue.put_nowait({'type':'error','message':detail if isinstance(detail,str) else detail['message'],
                             'trace':detail.get('trace',[]) if isinstance(detail,dict) else []})
        except Exception:
            queue.put_nowait({'type':'error','message':'教学回复未完成，请重试；声学结果仍可查看。'})
    async def events():
        task=asyncio.create_task(produce())
        try:
            while True:
                event=await queue.get()
                yield json.dumps(event,ensure_ascii=False)+'\n'
                if event['type'] in ('result','error'):
                    break
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(events(),media_type='application/x-ndjson',headers={'X-Accel-Buffering':'no'})


@app.get('/api/teacher/students')
def students(user: User=Depends(teacher)):
    with SessionLocal() as db:
        rows=db.scalars(select(User).where(User.teacher_id==user.id)).all()
        return {'students':[public_user(row) for row in rows]}


@app.get('/api/teacher/students/{student_id}/practices')
def student_practices(student_id: str,user: User=Depends(teacher)):
    with SessionLocal() as db:
        student=db.get(User,student_id)
        if not student or student.teacher_id!=user.id: raise HTTPException(404,'没有找到这位学习者。')
        rows=db.scalars(select(Practice).where(Practice.user_id==student_id).order_by(Practice.created_at.desc()).limit(50)).all()
        return {'student':public_user(student),'practices':[serialize_practice(row) for row in rows]}


@app.get('/')
def index():
    return FileResponse(ROOT/'frontend'/'index.html')


app.mount('/assets',StaticFiles(directory=ROOT/'frontend'),name='assets')
