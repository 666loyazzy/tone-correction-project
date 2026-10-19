import unittest

from fastapi.testclient import TestClient

from test_api import wave_bytes
from backend.main import app
from backend.exercises import EXERCISES


class SyllableTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.post('/api/auth/guest').raise_for_status()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def practice(self, syllable=None, reference=None):
        data = {'tone': 3}
        if reference is not None:
            data['reference_id'] = reference
        if syllable is not None:
            data['syllable'] = syllable
        queued = self.client.post('/api/analyses', data=data,
            files={'file': ('practice.wav', wave_bytes())})
        queued.raise_for_status()
        row = self.client.get('/api/practices/' + queued.json()['id']).json()
        self.assertEqual(row['state'], 'completed')
        return row

    def test_practice_scores_memory_and_teaching_follow_the_saved_syllable(self):
        first = self.practice('ba')
        legacy = self.practice()
        latest = self.practice('ba')
        self.assertEqual(latest['result']['syllable'], 'ba')
        self.assertEqual(legacy['syllable'], 'ma')
        scores = self.client.get('/api/scores?tone=3&syllable=ba').json()['records']
        self.assertEqual([row['id'] for row in scores], [first['id'], latest['id']])
        self.assertEqual(scores[-1]['change'], 0)
        self.assertNotEqual(latest['result']['score']['comparison_key'], legacy['result']['score']['comparison_key'])
        memory = self.client.get('/api/memory').json()['facts']
        self.assertEqual(memory[0]['syllable'], 'ba')
        reply = self.client.post('/api/chat', json={'message': '怎样复练？',
            'analysis_id': latest['id'], 'syllable': 'ma'}).json()
        self.assertEqual(reply['evidence']['comparison']['practice_id'], first['id'])
        self.assertEqual(reply['evidence']['syllable'], 'ba')
        self.assertIn('bǎ', reply['evidence']['experiment'])
        self.assertNotIn('mǎ', reply['text'])
        self.assertEqual(len(reply['trace']), 6)

    def test_all_exercises_filter_references_and_reject_a_different_syllable(self):
        self.assertEqual(len(EXERCISES), 12)
        self.assertEqual(len(self.client.get('/assets/exercises.json').json()), 12)
        for syllable, item in EXERCISES.items():
            self.assertEqual(len(item['pinyin']), 4)
            for tone in range(1, 5):
                references = self.client.get(f'/api/references?tone={tone}&syllable={syllable}').json()['references']
                self.assertTrue(references)
                self.assertTrue(references[0].get('scoring_ready', True))
                self.assertTrue(all(ref['tone'] == tone and ref.get('syllable', 'ma') == syllable for ref in references))
        rejected = self.client.post('/api/analyses', data={'tone': 3, 'syllable': 'ba', 'reference_id': 'openlearn-ma-3'},
            files={'file': ('wrong-reference.wav', wave_bytes())})
        self.assertEqual(rejected.status_code, 404)
        self.assertEqual(self.client.get('/api/practices').json()['practices'], [])

    def test_unknown_syllables_are_rejected_at_each_input_boundary(self):
        self.assertEqual(self.client.get('/api/references?syllable=invalid').status_code, 400)
        self.assertEqual(self.client.get('/api/scores?syllable=invalid').status_code, 400)
        self.assertEqual(self.client.post('/api/chat', json={'message': '练习', 'syllable': 'invalid'}).status_code, 400)
        response = self.client.post('/api/analyses', data={'syllable': 'invalid'},
            files={'file': ('invalid.wav', wave_bytes())})
        self.assertEqual(response.status_code, 400)
