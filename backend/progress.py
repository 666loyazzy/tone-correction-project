from __future__ import annotations

import asyncio
import shutil
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from .auth import current_user, teacher
from .database import DATA_DIR, Practice, SessionLocal, ToneReference, User, identifier
from .acoustics import AudioError, analyze, decode_audio
from .scoring import ensure_score, normalized_curve, template

router=APIRouter(prefix='/api')


def professional_audio_path(ref_id):
    runtime=DATA_DIR/'references'/ref_id/'audio.wav'
    return runtime if runtime.exists() else Path(__file__).parent/'reference_audio'/f'{ref_id}.wav'


def professional_references(tone):
    path=Path(__file__).with_name('reference_catalog.json')
    catalog=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    return [reference for reference in catalog if reference['tone']==tone and
            professional_audio_path(reference['id']).exists()]


def reference_owner(user):
    return user.id if user.role=='teacher' else user.teacher_id


def selected_reference(user, tone, requested=None):
    if requested and requested==f'target-{tone}':return template(tone)
    professional=professional_references(tone)
    if requested:
        match=next((reference for reference in professional if reference['id']==requested),None)
        if match:return match
    with SessionLocal() as db:
        query=select(ToneReference).where(ToneReference.teacher_id==reference_owner(user),ToneReference.tone==tone)
        if requested:query=query.where(ToneReference.id==requested)
        row=db.scalar(query.order_by(ToneReference.created_at.desc()).limit(1))
        if requested and not row:raise HTTPException(404,'没有找到可使用的同声调示范。')
        return row.curve if row else (professional[0] if professional else template(tone))


@router.get('/references')
def references(tone: int=3,user: User=Depends(current_user)):
    if tone not in range(1,5):raise HTTPException(400,'请选择一至四声。')
    with SessionLocal() as db:
        rows=db.scalars(select(ToneReference).where(ToneReference.teacher_id==reference_owner(user),ToneReference.tone==tone)
                        .order_by(ToneReference.created_at.desc()).limit(20)).all()
        return {'references':[row.curve for row in rows]+professional_references(tone)+[template(tone)]}


@router.post('/references',status_code=201)
async def upload_reference(file: Annotated[UploadFile,File()],tone: Annotated[int,Form(ge=1,le=4)]=3,
                           name: Annotated[str,Form(max_length=60)]='教师示范',user: User=Depends(teacher)):
    ref_id=identifier(); directory=DATA_DIR/'references'/ref_id; directory.mkdir(parents=True)
    saved=False
    try:
        size=0
        with (directory/'source.audio').open('wb') as output:
            while chunk:=await file.read(65536):
                size+=len(chunk)
                if size>12*1024*1024:raise HTTPException(413,'示范录音最多 12 MB。')
                output.write(chunk)
        if not size:raise HTTPException(400,'请选择完整的示范录音。')
        samples,rate=await asyncio.to_thread(decode_audio,directory/'source.audio',directory/'audio.wav')
        result=await asyncio.to_thread(analyze,samples,rate,tone,75,600,None,None)
        if result['score']['total'] is None:raise HTTPException(400,'示范的有效基频不足或存在质量问题，请换一段清晰的完整音节。')
        curve={**normalized_curve(result),'id':ref_id,'tone':tone,'kind':'recorded',
               'name':name.strip() or '教师示范','version':'recorded-1','source_url':None,
               'audio_url':f'/api/references/{ref_id}/audio','note':'教师上传录音，由 Praat 实际提取。',
               'time_s':result['time_s'],'f0_hz':result['f0_hz'],'algorithm':result['algorithm']}
        with SessionLocal() as db:
            db.add(ToneReference(id=ref_id,teacher_id=user.id,tone=tone,name=curve['name'],curve=curve));db.commit()
        saved=True;return curve
    except AudioError as exc:raise HTTPException(400,str(exc)) from None
    finally:
        await file.close()
        if not saved:shutil.rmtree(directory,ignore_errors=True)


@router.get('/references/{ref_id}/audio')
def reference_audio(ref_id: str,user: User=Depends(current_user)):
    for tone in range(1,5):
        if any(reference['id']==ref_id for reference in professional_references(tone)):
            return FileResponse(professional_audio_path(ref_id),media_type='audio/wav')
    with SessionLocal() as db:
        row=db.get(ToneReference,ref_id)
        if not row or row.teacher_id!=reference_owner(user):raise HTTPException(404,'没有找到可访问的示范录音。')
    return FileResponse(DATA_DIR/'references'/ref_id/'audio.wav',media_type='audio/wav')


@router.get('/scores')
def scores(tone: int=3,user: User=Depends(current_user)):
    if tone not in range(1,5):raise HTTPException(400,'请选择一至四声。')
    with SessionLocal() as db:
        rows=db.scalars(select(Practice).where(Practice.user_id==user.id,Practice.tone==tone,Practice.state=='completed')
                        .order_by(Practice.created_at.desc()).limit(200)).all()
        records=[]
        for row in reversed(rows):
            result=ensure_score(row.result)
            if not result:continue
            if result is not row.result:row.result=result
            score=result['score']
            records.append({'id':row.id,'created_at':row.created_at,'tone':row.tone,'score':score['total'],
                'components':score['components'],'comparison_key':score['comparison_key'],
                'reference_name':score['reference']['name'],'reference_id':score['reference']['id'],
                'version':score['version'],'reason':score['reason']})
        db.commit()
    # Changes are compared only within the same reference and extraction configuration.
    previous={};first={}
    for record in records:
        value=record['score'];key=record['comparison_key']
        record['change']=None if value is None or key not in previous else round(value-previous[key],1)
        record['since_first']=None if value is None else round(value-first.setdefault(key,value),1)
        if value is not None:previous[key]=value
    return {'records':records,'tone':tone}
