"""Shared single-syllable practice material; legacy practices default to ma."""
import json
from pathlib import Path

from fastapi import HTTPException

EXERCISES = {item['id']: item for item in json.loads(
    (Path(__file__).resolve().parents[1] / 'frontend' / 'exercises.json').read_text(encoding='utf-8'))}


def validate_syllable(value):
    if value not in EXERCISES:
        raise HTTPException(400, '请选择练习列表中的音节。')
    return value


def practice_syllable(practice):
    return practice.parameters.get('syllable', 'ma')


def pronunciation(syllable, tone):
    return EXERCISES[syllable]['pinyin'][tone - 1]
