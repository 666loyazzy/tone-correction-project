"""Bundle licensed native recordings and their measured Praat curves."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlparse, unquote

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.acoustics import analyze, decode_audio
from backend.exercises import EXERCISES
from backend.scoring import normalized_curve, aligned


class PlainText(HTMLParser):
    def __init__(self, value):
        super().__init__()
        self.parts = []
        self.feed(value)

    def handle_data(self, value):
        self.parts.append(value)

    def __str__(self):
        return ' '.join(' '.join(self.parts).split())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata', type=Path, required=True,
        help='Commons imageinfo query with url and extmetadata for the original files')
    parser.add_argument('--downloads', type=Path, required=True)
    parser.add_argument('--syllables', nargs='+', choices=list(EXERCISES),
        help='Import only these syllables, preserving other existing references')
    args = parser.parse_args()
    selected = set(args.syllables or EXERCISES)
    pages = {page['title']: page for page in json.loads(args.metadata.read_text(encoding='utf-8'))['query']['pages']}
    args.downloads.mkdir(parents=True, exist_ok=True)
    audio = ROOT / 'backend' / 'reference_audio'
    missing = []
    jobs = []
    for syllable, exercise in EXERCISES.items():
        if syllable == 'ma' or syllable not in selected:
            continue
        for tone, pinyin in enumerate(exercise['pinyin'], 1):
            page = pages.get('File:Zh-' + pinyin + '.ogg')
            if not page or page.get('missing'):
                missing.append(pinyin)
                continue
            actual = unquote(urlparse(page['imageinfo'][0]['url']).path.rsplit('/', 1)[-1]).rsplit('.', 1)[0]
            if actual != 'Zh-' + pinyin:
                missing.append(pinyin)
                continue
            jobs.append((syllable, tone, pinyin, page['imageinfo'][0]))

    with httpx.Client(timeout=30, follow_redirects=True, headers={
        'User-Agent': 'ToneCorrectionProject/1.0 (https://github.com/666loyazzy/tone-correction-project)'
    }) as client:
        def load(job):
            syllable, tone, pinyin, info = job
            metadata = info['extmetadata']
            license_name = metadata['LicenseShortName']['value']
            assert license_name in ('CC BY 2.0 fr', 'CC BY-SA 3.0 us'), license_name
            assert urlparse(info['url']).hostname == 'upload.wikimedia.org'
            ref_id = f'commons-{syllable}-{tone}'
            original = args.downloads / (ref_id + '.ogg')
            if not original.exists():
                for attempt in range(3):
                    response = client.get(info['url'].split('?')[0])
                    if response.status_code != 429:
                        break
                    time.sleep(20)
                response.raise_for_status()
                assert 0 < len(response.content) <= 5 * 1024 * 1024
                original.write_bytes(response.content)
                time.sleep(1)
            samples, rate = decode_audio(original, audio / (ref_id + '.wav'))
            result = analyze(samples, rate, tone, 75, 600, None, None, syllable=syllable)
            if not result['features'] or result['quality']['valid_frames'] < 8:
                (audio / (ref_id + '.wav')).unlink()
                missing.append(pinyin)
                print('Skipped insufficient F0: ' + ref_id, flush=True)
                return None
            license_url = metadata['LicenseUrl']['value'].replace('http://', 'https://')
            assert urlparse(license_url).hostname == 'creativecommons.org'
            curve = normalized_curve(result)
            scoring_ready = int(np.isfinite(aligned(curve, np.linspace(0, 1, 41))).sum()) >= 36
            return {**curve, 'scoring_ready':scoring_ready, 'id': ref_id, 'tone': tone, 'syllable': syllable,
                'kind': 'professional', 'name': '真人示范 · ' + pinyin, 'version': 'commons-1',
                'source_url': info['descriptionurl'], 'audio_url': f'/api/references/{ref_id}/audio',
                'note': '维基共享资源收录的普通话真人发音；转为单声道 WAV，Praat 实际提取声调曲线。',
                'attribution': str(PlainText(metadata['Artist']['value'])),
                'license': license_name, 'license_url': license_url,
                'original_audio_url': info['url'], 'time_s': result['time_s'], 'f0_hz': result['f0_hz'],
                'algorithm': result['algorithm'], 'quality': result['quality']}

        references = [ref for job in jobs if (ref := load(job)) is not None]
    catalog_path = ROOT / 'backend' / 'reference_catalog.json'
    catalog = [ref for ref in json.loads(catalog_path.read_text(encoding='utf-8'))
               if not (ref['id'].startswith('commons-') and ref.get('syllable', 'ma') in selected)]
    catalog_path.write_text(json.dumps(catalog + references, ensure_ascii=False, indent=2), encoding='utf-8')
    (audio / 'COMMONS-ATTRIBUTION.txt').write_text('\n\n'.join(
        f"{ref['id']}: {ref['name']}\n{ref['source_url']}\n{ref['attribution']}\n"
        f"{ref['license']} {ref['license_url']}\nChanges: mono PCM WAV conversion and Praat curve extraction."
        for ref in catalog + references if ref['id'].startswith('commons-')) + '\n', encoding='utf-8')
    print(json.dumps({'native_recordings_added': len(references), 'schematic_only': missing}, ensure_ascii=True))


if __name__ == '__main__':
    main()
