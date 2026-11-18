"""Validate normalized task outputs and expose conservative source checks."""
import re
from . import engine, policy

def strings(value, label):
    if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or len(x)>10000 for x in value):
        raise engine.EngineError(f"Invalid AI response: {label} must be a list of text.")

def review(data):
    if not isinstance(data, dict) or isinstance(data.get('score'), bool) or not isinstance(data.get('score'), int) or not 0 <= data['score'] <= 100 or not isinstance(data.get('verdict'), str):
        raise engine.EngineError('Invalid CV review response.')
    for key in ('issues','missing','questions'):
        strings(data.get(key), key)
    return data

def content(data):
    if not isinstance(data, dict):
        raise engine.EngineError('Invalid AI content response.')
    for key in ('summary', 'cover_letter'):
        if key in data and not isinstance(data[key], str):
            raise engine.EngineError(f'Invalid AI response: {key} must be text.')
    for key in ('skills','projects','experience'):
        if key in data and (not isinstance(data[key], list) or len(data[key])>100):
            raise engine.EngineError(f'Invalid AI response: {key} must be a list.')
    return data

def source_checks(text, source):
    """Numeric checks are warnings, never a factuality guarantee."""
    numbers = set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?%?', str(text)))
    known = set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?%?', source))
    warnings = [f'Number not found in candidate source: {n}' for n in sorted(numbers-known)]
    return {'status': 'review_required', 'warnings': warnings,
            'source_hash': policy.digest(source),
            'note': 'Review all claims against your source CV. Numeric checks cannot verify meaning or detect every invented claim.'}
# Canonical output schemas. Local validation remains mandatory for CLI/custom providers.
TEXT = {'type': 'string'}
TEXTS = {'type': 'array', 'items': TEXT}
def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
REVIEW_SCHEMA = obj({'score': {'type':'integer','minimum':0,'maximum':100}, 'verdict': TEXT,
                     'issues':TEXTS,'missing':TEXTS,'questions':TEXTS})
TAILOR_SCHEMA = obj({'summary':TEXT, 'cover_letter':TEXT,
    'skills':{'type':'array','items':{'type':'array','items':TEXT}},
    'projects':{'type':'array','items':obj({'header':TEXT,'date':TEXT,'bullets':TEXTS})},
    'fit':obj({'matches':TEXTS,'gaps':TEXTS})})

def schema_check(data, schema):
    from jsonschema import Draft202012Validator
    if next(Draft202012Validator(schema).iter_errors(data), None):
        raise engine.EngineError('AI response failed schema validation. No changes were saved.')
    return data

SKILL = obj({'category':TEXT,'items':TEXT})
EXPERIENCE = obj({'title':TEXT,'org':TEXT,'location':TEXT,'dates':TEXT,'bullets':TEXTS})
PROJECT = obj({'name':TEXT,'stack':TEXT,'dates':TEXT,'bullets':TEXTS})
def array(item):
    return {'type':'array','items':item}
IMPROVE_SCHEMA = obj({'summary':TEXT,'skills':array(SKILL),'experience':array(EXPERIENCE),'projects':array(PROJECT)})
IMPORT_SCHEMA = obj({**IMPROVE_SCHEMA['properties'],
    'personal':obj({'name':TEXT,'email':TEXT,'phone':TEXT,'location':TEXT,'links':TEXTS}),
    'nationality':TEXT,'education':array(obj({'degree':TEXT,'institution':TEXT,'dates':TEXT,'detail':TEXT})),
    'certifications':TEXTS,'languages':TEXTS})

def profile_content(data, schema):
    return schema_check({k:data[k] for k in schema['properties']},schema)
