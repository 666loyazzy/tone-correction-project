import unittest
import numpy as np
from fastapi.testclient import TestClient
from test_api import wave_bytes
from backend.main import app
from backend.scoring import template, evaluate, normalized_curve, aligned


class ScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.teacher=TestClient(app);cls.teacher.__enter__()
        response=cls.teacher.post('/api/auth/login',json={'username':'test_teacher','password':'test-password-123'})
        if response.status_code!=200:
            cls.teacher.post('/api/auth/register',json={'username':'test_teacher','password':'test-password-123','display_name':'教师'}).raise_for_status()
    @classmethod
    def tearDownClass(cls):cls.teacher.__exit__(None,None,None)
    def learner(self,name):
        invite=self.teacher.post('/api/auth/invites').json()['invite_code'];client=TestClient(app)
        client.post('/api/auth/register',json={'username':name,'password':'test-password-123','display_name':name,'invite_code':invite}).raise_for_status();return client
    def practice(self,client,reference=None):
        data={'tone':3}
        if reference:data['reference_id']=reference
        queued=client.post('/api/analyses',data=data,files={'file':('tone.wav',wave_bytes())})
        self.assertEqual(queued.status_code,202,queued.text)
        return client.get('/api/practices/'+queued.json()['id']).json()
    def test_correct_contours_beat_reversed_and_silence_is_unscored(self):
        for tone in (2,3,4):
            reference=template(tone);times=np.linspace(0,1,101)
            values=np.interp(times,reference['time_normalized'],reference['pitch_normalized'])
            result={'tone':tone,'time_s':times.tolist(),'f0_hz':(200*2**(values*8/12)).tolist(),
                    'features':{'start_f0_hz':200},'quality':{'valid_frames':101,'total_frames':101,'octave_jumps':0,'clipped_fraction':0},'algorithm':{'pitch_floor_hz':75}}
            correct=evaluate(result);reverse={**result,'f0_hz':(200*2**((1-values)*8/12)).tolist()}
            self.assertGreater(correct['total'],evaluate(reverse)['total']+25)
            exact={**normalized_curve(result),'id':'measured','kind':'recorded'}
            self.assertEqual(evaluate(result,exact)['total'],100)
            result['features']=None;result['f0_hz']=[None]*101;result['quality']['valid_frames']=0
            self.assertIsNone(evaluate(result)['total'])
        self.assertTrue(np.isnan(aligned({'time_normalized':[0,.25,.5,.75,1],'pitch_normalized':[0,0,None,1,1]},np.array([.5]))[0]))
    def test_score_history_preserves_reference_groups_ownership_and_delete(self):
        a=self.learner('scores_a');b=self.learner('scores_b')
        first=self.practice(a,'target-3');second=self.practice(a,'target-3');self.practice(b,'target-3')
        scores=a.get('/api/scores?tone=3').json()['records']
        self.assertEqual([row['id'] for row in scores],[first['id'],second['id']])
        self.assertEqual(scores[1]['change'],round(scores[1]['score']-scores[0]['score'],1))
        reference=self.teacher.post('/api/references',data={'tone':3,'name':'真实教师示范'},files={'file':('reference.wav',wave_bytes())})
        self.assertEqual(reference.status_code,201,reference.text)
        third=self.practice(a,reference.json()['id'])
        row=a.get('/api/scores?tone=3').json()['records'][-1]
        self.assertIsNone(row['change']);self.assertNotEqual(row['comparison_key'],scores[-1]['comparison_key'])
        self.assertEqual(a.get(reference.json()['audio_url']).content[:4],b'RIFF')
        self.assertEqual(b.post('/api/references',data={'tone':3},files={'file':('x.wav',wave_bytes())}).status_code,403)
        from backend.database import SessionLocal,User
        with SessionLocal() as db:
            owner=db.get(User,b.get('/api/auth/me').json()['user']['id']);owner.teacher_id=None;db.commit()
        self.assertEqual(b.get(reference.json()['audio_url']).status_code,404)
        self.assertEqual(a.delete('/api/practices/'+second['id']).status_code,200)
        self.assertNotIn(second['id'],[row['id'] for row in a.get('/api/scores?tone=3').json()['records']])
        self.assertEqual(len(third['result']['energy_dbfs']),len(third['result']['time_s']))
    def test_corrected_audio_is_real_private_and_preserves_duration(self):
        import io,wave
        a=self.learner('resynthesis_a');b=self.learner('resynthesis_b');row=self.practice(a,'target-3')
        url='/api/practices/'+row['id']+'/corrected-audio'
        response=a.get(url);self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
        with wave.open(io.BytesIO(response.content),'rb') as sound:
            self.assertAlmostEqual(sound.getnframes()/sound.getframerate(),row['result']['features']['selected_duration_s'],delta=.002)
            samples=np.frombuffer(sound.readframes(sound.getnframes()),dtype='<i2').astype(float)/32768
            from backend.acoustics import analyze
            corrected=analyze(samples,sound.getframerate(),3,75,600,None,None)
            self.assertLess(corrected['features']['min_f0_hz'],corrected['features']['start_f0_hz'])
            self.assertLess(corrected['features']['min_f0_hz'],corrected['features']['end_f0_hz'])
        self.assertEqual(b.get(url).status_code,404)
        self.assertEqual(a.get(url).content,response.content)
        from backend.tutor import validate_answer
        from backend.scientific import evidence
        measured=evidence(row['result']);score=row['result']['score']['total']
        self.assertEqual(validate_answer({'mode':'api','text':f'本次轮廓分为{score}分。','sources':[]},measured)['mode'],'api')
        self.assertEqual(validate_answer({'mode':'api','text':f'轮廓贴合占75%，方向占25%，本次轮廓分{score}分。','sources':[]},measured)['mode'],'api')
        self.assertEqual(validate_answer({'mode':'api','text':'本次轮廓分为999分。','sources':[]},measured)['mode'],'guarded')
