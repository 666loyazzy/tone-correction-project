import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from test_api import wave_bytes
from backend.main import app
from backend.database import Base, GuestQuota, LearningMemory, LoginSession, Practice, SessionLocal, User, engine, initialize
from backend.jobs import cleanup, practice_directory


class GuestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize()

    @classmethod
    def tearDownClass(cls):
        engine.dispose()

    def guest(self):
        client = TestClient(app)
        response = client.post('/api/auth/guest')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('Max-Age=86400', response.headers['set-cookie'])
        self.assertIn('SameSite=lax', response.headers['set-cookie'])
        return client, response.json()['user']

    def practice(self, client):
        response = client.post('/api/analyses', data={'tone':3}, files={'file':('trial.wav', wave_bytes())})
        self.assertEqual(response.status_code, 202, response.text)
        row = client.get('/api/practices/' + response.json()['id']).json()
        self.assertEqual(row['state'], 'completed')
        return row

    def test_guest_before_teacher_reuses_session_and_preserves_setup(self):
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with patch('backend.auth.SessionLocal', sessions), patch.dict('os.environ', {'SETUP_TOKEN':'guest-test-setup'}):
            client, user = self.guest()
            self.assertEqual(user['role'], 'guest')
            self.assertEqual(client.post('/api/auth/guest').json()['user']['id'], user['id'])
            self.assertTrue(client.get('/api/auth/status').json()['needs_setup'])
            self.assertEqual(client.post('/api/auth/login', json={'username':user['username'],'password':'guest-disabled'}).status_code, 401)
            self.assertEqual(client.post('/api/auth/guest', headers={'Origin':'https://another-site.example'}).status_code, 403)
            cookie = client.cookies.get('tone_correction_session')
            self.assertTrue(cookie)
            created = TestClient(app).post('/api/auth/register', json={
                'username':'first_teacher','password':'teacher-test-password','display_name':'教师','setup_token':'guest-test-setup'})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()['user']['role'], 'teacher')
            self.assertFalse(client.get('/api/auth/status').json()['needs_setup'])
            self.assertEqual(client.get('/api/auth/me').json()['user']['role'], 'guest')
        engine.dispose()

    def test_guest_can_analyze_chat_remember_and_play_without_shared_records(self):
        first, user = self.guest()
        second, other = self.guest()
        self.assertNotEqual(user['id'], other['id'])
        row = self.practice(first)
        self.assertTrue(all(step['status']=='completed' for step in row['trace']))
        path = '/api/practices/' + row['id']
        self.assertEqual(first.get(path + '/audio').status_code, 200)
        self.assertEqual(second.get(path).status_code, 404)
        self.assertEqual(second.get(path + '/audio').status_code, 404)
        self.assertEqual(second.get('/api/scores').json()['records'], [])
        self.assertEqual(first.get('/api/memory').json()['facts'][0]['practice_id'], row['id'])
        self.assertEqual(second.get('/api/memory').json()['facts'], [])
        self.assertEqual(first.get('/api/references/openlearn-ma-3/audio').status_code, 200)
        self.assertEqual(first.post('/api/auth/invites').status_code, 403)
        self.assertEqual(first.get('/api/teacher/students').status_code, 403)
        with patch.dict('os.environ', {'LLM_API_KEY':'', 'LLM_MODEL':''}):
            response = first.post('/api/chat', json={'message':'请分析这次发音','analysis_id':row['id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(all(step['status']=='completed' for step in response.json()['trace']))
        self.assertEqual(second.get('/api/conversations').json()['conversations'], [])

    def test_exhausted_trial_quota_blocks_work_but_formal_accounts_continue(self):
        client, user = self.guest()
        row = self.practice(client)
        with SessionLocal() as db:
            quota = db.get(GuestQuota, 'user-analysis:' + user['id'])
            quota.used = 20
            db.commit()
        denied = client.post('/api/analyses', data={'tone':3}, files={'file':('trial.wav', wave_bytes())})
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(len(client.get('/api/practices').json()['practices']), 1)
        formal = TestClient(app)
        response = formal.post('/api/auth/register', json={'username':'test_teacher','password':'test-password-123','display_name':'教师'})
        if response.status_code != 201:
            formal.post('/api/auth/login', json={'username':'test_teacher','password':'test-password-123'}).raise_for_status()
        self.assertEqual(formal.post('/api/auth/guest').json()['user']['role'], 'teacher')
        self.practice(formal)
        self.assertEqual(client.get('/api/practices/' + row['id']).status_code, 200)

    def test_expired_trial_clears_its_data_and_keeps_active_visitors(self):
        client, user = self.guest()
        row = self.practice(client)
        with SessionLocal() as db:
            db.get(User, user['id']).created_at = time.time() - 86401
            for session in db.scalars(select(LoginSession).where(LoginSession.user_id == user['id'])):
                session.expires_at = time.time() - 1
            db.commit()
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        response = client.post('/api/auth/guest')
        self.assertEqual(response.status_code, 200)
        active = response.json()['user']['id']
        self.assertNotEqual(active, user['id'])
        cleanup()
        with SessionLocal() as db:
            self.assertIsNone(db.get(User, user['id']))
            self.assertIsNone(db.get(Practice, row['id']))
            self.assertIsNone(db.get(LearningMemory, user['id']))
            self.assertIsNotNone(db.get(User, active))
        self.assertFalse(practice_directory(row['id']).exists())
        self.assertEqual(client.get('/api/auth/me').status_code, 200)
