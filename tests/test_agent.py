import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from test_api import wave_bytes
from backend.main import app
from backend.database import initialize
from backend.harness import AnalysisHarness


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize();cls.teacher=TestClient(app);cls.teacher.__enter__()
        response=cls.teacher.post('/api/auth/register',json={'username':'test_teacher','password':'test-password-123','display_name':'教师'})
        if response.status_code!=201:
            cls.teacher.post('/api/auth/login',json={'username':'test_teacher','password':'test-password-123'}).raise_for_status()

    @classmethod
    def tearDownClass(cls):
        cls.teacher.__exit__(None,None,None)

    def learner(self,name):
        invite=self.teacher.post('/api/auth/invites').json()['invite_code']
        client=TestClient(app)
        response=client.post('/api/auth/register',json={'username':name,'password':'test-password-123','display_name':name,'invite_code':invite})
        self.assertEqual(response.status_code,201,response.text)
        return client

    def practice(self,client):
        response=client.post('/api/analyses',data={'tone':3},files={'file':('voice.wav',wave_bytes())})
        self.assertEqual(response.status_code,202,response.text)
        return client.get('/api/practices/'+response.json()['id']).json()

    def test_ownership_and_teacher_authorization(self):
        a=self.learner('owner_a');b=self.learner('owner_b');row=self.practice(a)
        self.assertEqual(row['state'],'completed')
        self.assertEqual(b.get('/api/practices/'+row['id']).status_code,404)
        self.assertEqual(b.get('/api/practices/'+row['id']+'/audio').status_code,404)
        self.assertEqual(self.teacher.get('/api/practices/'+row['id']).status_code,200)
        self.assertEqual(b.delete('/api/practices/'+row['id']).status_code,404)

    def test_memory_controls_and_practice_delete(self):
        client=self.learner('memory_user');row=self.practice(client)
        memory=client.get('/api/memory').json();self.assertEqual(memory['facts'][0]['practice_id'],row['id'])
        client.put('/api/memory',json={'enabled':False,'goal':'观察二声上升段'})
        other=self.practice(client)
        self.assertEqual(client.get('/api/memory').json()['facts'],[])
        client.put('/api/memory',json={'enabled':True,'goal':'观察二声上升段'})
        self.assertEqual(len(client.get('/api/memory').json()['facts']),1)
        client.delete('/api/practices/'+row['id'])
        self.assertEqual(client.get('/api/memory').json()['facts'],[])
        self.assertEqual(client.get('/api/practices/'+row['id']+'/audio').status_code,404)

    def test_chat_has_harness_and_persistent_short_term_memory(self):
        client=self.learner('chat_user')
        result=client.post('/api/chat',json={'message':'三声怎么练？'}).json()
        self.assertEqual(len(result['trace']),6);self.assertTrue(result['sources'])
        session=client.get('/api/conversations/'+result['conversation_id']).json()
        self.assertEqual([m['role'] for m in session['messages']],['user','assistant'])
        client.post('/api/chat',json={'message':'再说一个练习','conversation_id':result['conversation_id']})
        self.assertEqual(len(client.get('/api/conversations/'+result['conversation_id']).json()['messages']),4)

    def test_model_receives_skills_and_owned_memory(self):
        client=self.learner('model_user')
        with patch.dict('os.environ',{'LLM_API_KEY':'test-only','LLM_MODEL':'test-model'}):
            with patch('backend.tutor.httpx.AsyncClient') as constructor:
                remote=constructor.return_value.__aenter__.return_value
                response=remote.post.return_value
                response.raise_for_status=lambda:None
                response.json=lambda:{'choices':[{'message':{'content':'配合向上手势，再完整读一次。'}}]}
                remote.post=AsyncMock(return_value=response)
                result=client.post('/api/chat',json={'message':'怎么练二声？','tone':2})
                self.assertEqual(result.status_code,200,result.text)
                payload=remote.post.call_args.kwargs['json']
                self.assertIn('name: tone-teaching',payload['messages'][0]['content'])
                self.assertIn('learning_memory',payload['messages'][1]['content'])

    def test_harness_refuses_skipping_required_steps(self):
        harness=AnalysisHarness('not-a-practice')
        with self.assertRaises(RuntimeError):harness.begin('praat.extract')
        with self.assertRaises(RuntimeError):harness.complete()

    def test_cookie_csrf_and_logout(self):
        client=self.learner('session_user')
        cookie=client.cookies.get('tone_correction_session');self.assertTrue(cookie)
        response=client.post('/api/chat',headers={'Origin':'https://another-site.example'},json={'message':'你好'})
        self.assertEqual(response.status_code,403)
        client.post('/api/auth/logout')
        self.assertEqual(client.get('/api/memory').status_code,401)
