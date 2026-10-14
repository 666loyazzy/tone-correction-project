from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///' + str(DATA_DIR / 'tone_correction.sqlite3'))
engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                      connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
if DATABASE_URL.startswith('sqlite'):
    @event.listens_for(engine, 'connect')
    def sqlite_settings(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA journal_mode=WAL')
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def identifier():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(12), default='learner')
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0)
    teacher_id: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Invite(Base):
    __tablename__ = 'invites'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[float] = mapped_column(Float)
    uses_remaining: Mapped[int] = mapped_column(Integer, default=1)


class Setting(Base):
    __tablename__ = 'settings'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Practice(Base):
    __tablename__ = 'practices'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    tone: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(16), default='queued', index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    parameters: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace: Mapped[list] = mapped_column(JSON, default=list)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(80), default='声调练习')
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class ToneReference(Base):
    __tablename__ = 'tone_references'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    teacher_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    tone: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    curve: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    sources: Mapped[list] = mapped_column(JSON, default=list)


class LearningMemory(Base):
    __tablename__ = 'learning_memories'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    goal: Mapped[str] = mapped_column(Text, default='练习孤立汉语声调，重点观察二声和三声')
    facts: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


def initialize():
    Base.metadata.create_all(engine)
