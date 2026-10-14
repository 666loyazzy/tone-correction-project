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
                self.assertIn('name: scientific-response',payload['messages'][0]['content'])
                self.assertIn('learning_memory',payload['messages'][1]['content'])

    def test_harness_refuses_skipping_required_steps(self):
        harness=AnalysisHarness('not-a-practice')
        with self.assertRaises(RuntimeError):harness.begin('praat.extract')
        with self.assertRaises(RuntimeError):harness.complete()

    def test_scientific_report_uses_owned_measurements_and_comparison(self):
        client=self.learner('science_user')
        first=self.practice(client)
        other=self.learner('science_other')
        self.practice(other)
        second=self.practice(client)
        response=client.post('/api/chat',json={'message':'生成科学报告','tone':2,'analysis_id':second['id']})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json(); measured=data['evidence']
        self.assertEqual(measured['tone'],3)
        self.assertEqual(measured['comparison']['practice_id'],first['id'])
        self.assertEqual(measured['features'],second['result']['features'])
        self.assertEqual(len(measured['regions']),3)
        self.assertEqual(sum(r['total_frames'] for r in measured['regions']),second['result']['quality']['total_frames'])
        self.assertIn('下一轮练习实验',data['text'])
        self.assertIn('半音',data['text'])
        self.assertTrue(all(stage['finished_at']>=stage['started_at'] for stage in data['trace']))
        self.assertTrue(all(stage['skill_version'] and stage['summary'] for stage in data['trace']))
        self.assertEqual(other.post('/api/chat',json={'message':'生成报告','analysis_id':second['id']}).status_code,404)

    def test_scientific_evidence_preserves_missing_frames_and_parameter_limits(self):
        from backend.scientific import evidence, local_report
        from copy import deepcopy
        client=self.learner('science_quality')
        row=self.practice(client); result=deepcopy(row['result'])
        result['f0_hz']=[None]*len(result['f0_hz']);result['features']=None
        result['quality']['valid_frames']=0
        measured=evidence(result)
        self.assertTrue(all(r['median_f0_hz'] is None for r in measured['regions']))
        self.assertEqual(measured['quality']['voiced_percent'],0)
        self.assertNotIn('pitch_range_semitones',measured)
        self.assertIn('有效基频不足',local_report(measured))
        from backend.tutor import measured_numbers_only
        live=evidence(row['result'])
        self.assertTrue(measured_numbers_only(f"最低 {row['result']['features']['min_f0_hz']:.1f} Hz",live))
        self.assertFalse(measured_numbers_only('把最低点保持在40–50%，再增加1–2半音',live))
        from backend.tutor import validate_answer
        wrong={'mode':'api','text':f"最低点位于选区{live['features']['minimum_position']*100:.1f}%",'sources':[]}
        self.assertEqual(validate_answer(wrong,live)['mode'],'guarded')
        prior=deepcopy(row);prior['result']['algorithm']['pitch_floor_hz']=90
        compared=evidence(row['result'],prior)
        self.assertFalse(compared['comparison']['matching_parameters'])
        self.assertEqual(compared['comparison']['changes'],{})

    def test_model_scientific_context_and_failed_step(self):
        import httpx
        import json
        client=self.learner('science_model'); row=self.practice(client)
        with patch.dict('os.environ',{'LLM_API_KEY':'test-only','LLM_MODEL':'deepseek-flash','LLM_BASE_URL':'https://api.deepseek.com'}):
            with patch('backend.tutor.httpx.AsyncClient') as constructor:
                remote=constructor.return_value.__aenter__.return_value
                response=httpx.Response(200,json={'choices':[{'message':{'content':'诊断为环甲肌无力，评分为99'}}]},request=httpx.Request('POST','https://api.deepseek.com/chat/completions'))
                remote.post=AsyncMock(return_value=response)
                data=client.post('/api/chat',json={'message':'科学报告','analysis_id':row['id']}).json()
                self.assertEqual(data['mode'],'guarded');self.assertNotIn('评分为99',data['text'])
                payload=remote.post.call_args.kwargs['json']
                context=json.loads(payload['messages'][1]['content'].split('：',1)[1])
                self.assertEqual(context['scientific_evidence']['features'],row['result']['features'])
                self.assertEqual(payload['thinking'],{'type':'enabled'})
                self.assertGreater(payload['max_tokens'],800)
                remote.post=AsyncMock(return_value=httpx.Response(401,request=httpx.Request('POST','https://api.deepseek.com/chat/completions')))
                failed=client.post('/api/chat',json={'message':'科学报告','analysis_id':row['id']})
                self.assertEqual(failed.status_code,503)
                trace=failed.json()['detail']['trace']
                self.assertEqual(trace[3]['status'],'failed');self.assertEqual(trace[4]['status'],'pending')
                self.assertNotIn('test-only',failed.text)

    def test_cookie_csrf_and_logout(self):
        client=self.learner('session_user')
        cookie=client.cookies.get('tone_correction_session');self.assertTrue(cookie)
        response=client.post('/api/chat',headers={'Origin':'https://another-site.example'},json={'message':'你好'})
        self.assertEqual(response.status_code,403)
        client.post('/api/auth/logout')
        self.assertEqual(client.get('/api/memory').status_code,401)

    def test_stream_reports_actual_progress_and_mandatory_skill(self):
        import json
        from backend.harness import MANIFEST, skill_text
        import hashlib
        client=self.learner('stream_user'); row=self.practice(client)
        response=client.post('/api/chat/stream',json={'message':'生成科学报告','analysis_id':row['id'],'report':True})
        self.assertEqual(response.status_code,200)
        events=[json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]['type'],'result')
        self.assertTrue(any(e['type']=='trace' and e['trace'][3]['status']=='running' and e['trace'][4]['status']=='pending' for e in events))
        data=events[-1]['data']
        expected=hashlib.sha256(skill_text('scientific-response').encode()).hexdigest()[:12]
        self.assertEqual(data['trace'][2]['skill_version'],expected)
        self.assertIn('scientific-response',data['trace'][2]['summary'])
        self.assertTrue(all(e['status']=='completed' for e in data['trace']))
        harness=AnalysisHarness(None,MANIFEST['chat_workflow'])
        with self.assertRaises(RuntimeError):harness.begin('chat.answer')
