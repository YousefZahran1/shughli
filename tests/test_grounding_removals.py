import json
from core import tailor, profile as P


def test_real_skill_aliases_survive():
    source='Skills: PostgreSQL, JavaScript, scikit-learn, C++'
    skills=[['Tools','Postgres, JS, SCIKIT LEARN, C++']]
    assert tailor._ground_skills(skills,source,'')==skills


def test_invented_skill_and_substring_dropped():
    assert tailor._ground_skills([['Tools','Go, Kubernetes']], 'Google analyst using SQL','')==[]


def test_dropped_items_return_in_package(stub_generate):
    profile=P.normalize_profile({'skills':[{'category':'Tools','items':'PostgreSQL'}],
        'projects':[{'name':'Real project','stack':'SQL','bullets':['Built reports']} ]})
    stub_generate.reply=json.dumps({'summary':'Analyst','cover_letter':'Hello',
        'skills':[['Tools','Postgres, Kubernetes']],
        'projects':[{'header':'Imaginary project','date':'','bullets':['Invented']}], 'fit':{}})
    job={'id':'j','company':'Example','title':'Analyst','location':'Remote','description':'SQL'}
    package=tailor.build_package(job,profile)
    assert package['skills']==[['Tools: ','Postgres']]
    assert package['dropped_items']['skills'][0]['item']=='Kubernetes'
    assert package['dropped_items']['projects'][0]['header']=='Imaginary project'
    assert tailor.load_saved_package(job,profile)['dropped_items']==package['dropped_items']
