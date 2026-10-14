from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .database import Invite, LoginSession, SessionLocal, Setting, User

router = APIRouter(prefix='/api/auth', tags=['账户'])
COOKIE_NAME = 'tone_correction_session'
SESSION_SECONDS = 7 * 24 * 3600


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(value: str, encoded: str) -> bool:
    try:
        _, iterations, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def public_user(user: User) -> dict:
    return {'id': user.id, 'username': user.username, 'display_name': user.display_name, 'role': user.role}


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME, '')
    if not token:
        raise HTTPException(401, '请先登录。')
    with SessionLocal() as db:
        login = db.get(LoginSession, token_hash(token))
        if not login or login.expires_at <= time.time():
            raise HTTPException(401, '登录已过期，请重新登录。')
        user = db.get(User, login.user_id)
        if not user:
            raise HTTPException(401, '请重新登录。')
        return user


def teacher(user: User = Depends(current_user)) -> User:
    if user.role != 'teacher':
        raise HTTPException(403, '此操作需要教师账号。')
    return user


def create_session(user: User, request: Request, response: Response):
    token = secrets.token_urlsafe(32)
    with SessionLocal() as db:
        db.add(LoginSession(token_hash=token_hash(token), user_id=user.id,
                            expires_at=time.time() + SESSION_SECONDS))
        db.commit()
    secure = os.getenv('COOKIE_SECURE', '').lower() == 'true' or request.url.scheme == 'https'
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        samesite='lax', secure=secure, path='/')


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    display_name: str = Field(min_length=1, max_length=50)
    invite_code: str = Field(default='', max_length=200)
    setup_token: str = Field(default='', max_length=200)


@router.get('/status')
def status():
    with SessionLocal() as db:
        needs_setup = db.scalar(select(User.id).limit(1)) is None
    return {'needs_setup': needs_setup, 'setup_token_required': bool(os.getenv('SETUP_TOKEN'))}


@router.post('/register', status_code=201)
def register(body: Registration, request: Request, response: Response):
    with SessionLocal() as db:
        first = db.scalar(select(User.id).limit(1)) is None
        invite = None
        if first:
            expected = os.getenv('SETUP_TOKEN', '')
            if expected:
                if not hmac.compare_digest(body.setup_token, expected):
                    raise HTTPException(403, '初始化口令不正确。')
            elif request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
                raise HTTPException(403, '远程初始化需要服务器配置 SETUP_TOKEN。')
            db.add(Setting(key='initialized', value='true'))
        else:
            invite = db.scalar(select(Invite).where(Invite.token_hash == token_hash(body.invite_code)).with_for_update())
            if not invite or invite.expires_at <= time.time() or invite.uses_remaining < 1:
                raise HTTPException(403, '邀请码无效或已使用，请联系教师。')
            invite.uses_remaining -= 1
        user = User(username=body.username.lower(), display_name=body.display_name,
                    password_hash=password_hash(body.password), role='teacher' if first else 'learner',
                    teacher_id=invite.created_by if invite else None)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '账号已存在或初始化已完成，请尝试登录。') from None
    create_session(user, request, response)
    return {'user': public_user(user)}


@router.post('/login')
def login(body: Credentials, request: Request, response: Response):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == body.username.lower()).with_for_update())
        if user and user.locked_until > time.time():
            raise HTTPException(429, '尝试次数较多，请在 15 分钟后重试。')
        if not user or not verify_password(body.password, user.password_hash):
            if user:
                user.failed_logins += 1
                if user.failed_logins >= 5:
                    user.locked_until = time.time() + 900
                    user.failed_logins = 0
                db.commit()
            else:
                hashlib.pbkdf2_hmac('sha256', body.password.encode(), b'tone_correction-no-account', 600000)
            raise HTTPException(401, '账号或密码不正确。')
        user.failed_logins = 0
        user.locked_until = 0
        db.commit()
    create_session(user, request, response)
    return {'user': public_user(user)}


@router.get('/me')
def me(user: User = Depends(current_user)):
    return {'user': public_user(user)}


@router.post('/logout')
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME, '')
    with SessionLocal() as db:
        session = db.get(LoginSession, token_hash(token))
        if session:
            db.delete(session)
            db.commit()
    response.delete_cookie(COOKIE_NAME, path='/')
    return {'ok': True}


@router.post('/invites', status_code=201)
def invite(user: User = Depends(teacher)):
    code = secrets.token_urlsafe(18)
    with SessionLocal() as db:
        db.add(Invite(token_hash=token_hash(code), created_by=user.id,
                      expires_at=time.time() + 7 * 86400, uses_remaining=1))
        db.commit()
    return {'invite_code': code, 'expires_in_days': 7, 'uses': 1}
