import io
import subprocess
import tempfile
import os
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix='tone_correction-unit-')
os.environ['DATA_DIR'] = TEST_DIRECTORY.name

import numpy as np
import httpx
from fastapi.testclient import TestClient

from backend.main import app
from backend.database import initialize


def wave_bytes(silent=False):
    sr = 44100
    t = np.arange(sr) / sr
    samples = np.zeros(sr) if silent else 0.2 * np.sin(2 * np.pi * 200 * t)
    output = io.BytesIO()
    with wave.open(output, 'wb') as file:
        file.setnchannels(1); file.setsampwidth(2); file.setframerate(sr)
        file.writeframes((samples * 32767).astype('<i2').tobytes())
    return output.getvalue()


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize()
        cls.client = TestClient(app)
        cls.client.__enter__()
        first=cls.client.post('/api/auth/register',json={
            'username':'test_teacher','password':'test-password-123','display_name':'测试教师'})
        if first.status_code!=201:
            cls.client.post('/api/auth/login',json={'username':'test_teacher','password':'test-password-123'}).raise_for_status()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)

    def setUp(self):
        self.client = type(self).client

    def post(self, data=None, audio=None, filename='input.wav'):
        response=self.client.post('/api/analyses', data=data or {'tone': 3},
                                  files={'file': (filename, wave_bytes() if audio is None else audio)})
        if response.status_code==202:
            row=self.client.get('/api/practices/'+response.json()['id']).json()
            if row['state']=='completed':
                self.assertTrue(all(stage['status']=='completed' for stage in row['trace']))
                return httpx.Response(200,json=row['result'])
            return httpx.Response(400,json={'detail':row['error']})
        return response

    def test_known_frequency_and_timestamps(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        voiced = [value for value in data['f0_hz'] if value is not None]
        self.assertAlmostEqual(float(np.median(voiced)), 200, delta=1)
        self.assertEqual(len(data['time_s']), len(data['f0_hz']))
        self.assertAlmostEqual(data['time_s'][1]-data['time_s'][0], 0.01, places=5)
        self.assertEqual(data['algorithm']['praat_version'], '6.1.38')

    def test_silence_has_no_fabricated_features(self):
        data = self.post(audio=wave_bytes(True)).json()
        self.assertTrue(all(value is None for value in data['f0_hz']))
        self.assertIsNone(data['features'])
        self.assertTrue(data['quality']['warnings'])

    def test_manual_selection_keeps_absolute_time(self):
        data = self.post({'tone': 2, 'start_s': 0.25, 'end_s': 0.75}).json()
        self.assertGreater(data['time_s'][0], 0.25)
        self.assertLess(data['time_s'][-1], 0.75)
        self.assertFalse(data['selection']['automatic'])

    def test_invalid_crop_and_pitch_rejected(self):
        for data in [{'start_s': 0.8, 'end_s': 0.4}, {'start_s': 0},
                     {'pitch_floor': 600, 'pitch_ceiling': 75}, {'pitch_floor': 'nan'}]:
            with self.subTest(data=data):
                self.assertEqual(self.post(data).status_code, 400)

    def test_empty_and_invalid_audio(self):
        self.assertEqual(self.post(audio=b'').status_code, 400)
        self.assertEqual(self.post(audio=b'not an audio file', filename='fake.wav').status_code, 400)

    def test_webm_and_m4a_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root/'source.wav'; source.write_bytes(wave_bytes())
            for extension, codec in [('webm', 'libopus'), ('m4a', 'aac')]:
                target = root/f'encoded.{extension}'
                subprocess.run(['ffmpeg','-v','error','-i',str(source),'-c:a',codec,str(target)],check=True)
                response = self.post(audio=target.read_bytes(), filename=target.name)
                self.assertEqual(response.status_code,200,response.text)
                values=[v for v in response.json()['f0_hz'] if v]
                self.assertAlmostEqual(float(np.median(values)),200,delta=2)

    def test_local_guidance_without_api_key(self):
        with patch.dict('os.environ', {'LLM_API_KEY':'', 'LLM_MODEL':''}):
            data=self.client.post('/api/chat',json={'message':'怎么录音？'}).json()
            self.assertEqual(data['mode'],'local')

    def test_page_and_security_headers(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['permissions-policy'],'microphone=(self), camera=()')
        self.assertIn('声调纠正系统',response.text)


if __name__ == '__main__':
    unittest.main()
