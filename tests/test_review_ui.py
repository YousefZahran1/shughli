import json
import subprocess
from pathlib import Path

# Execute the real renderer against a minimal DOM; no browser or network needed.
def render_package(package):
    source = Path('static/app.js').read_text(encoding='utf-8')
    function = source[source.index('async function tailor()'):source.index('// ================= CV COACH')]
    script = '''const vm=require('node:vm');
const nodes={};
const context={CURRENT:{id:'j'},HAS_PKG:false,TAILOR_LABEL:'Tailor',esc:s=>String(s),
  $:id=>nodes[id]??=( {textContent:'',innerHTML:'',classList:{add(){},remove(){}},disabled:false}),
  fetch:async()=>({json:async()=>({ok:true,package:PACKAGE})})};
vm.createContext(context);vm.runInContext(FUNCTION,context);
context.tailor().then(()=>process.stdout.write(JSON.stringify(nodes)));
'''.replace('PACKAGE', json.dumps(package)).replace('FUNCTION', json.dumps(function))
    result=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)

def test_cover_copy_excludes_notes():
    nodes=render_package({'cover_letter':'Dear team, my real letter.', 'source_checks':{'note':'Review these facts','warnings':['Metric unsupported']}})
    assert nodes['#g-cover']['textContent']=='Dear team, my real letter.'
    assert 'Metric unsupported' in nodes['#g-review-notes']['textContent']
    assert 'id="g-review-notes"' in Path('static/index.html').read_text(encoding='utf-8')

def test_dropped_items_are_visible():
    nodes=render_package({'cover_letter':'Only letter', 'dropped_items':{'skills':[{'item':'Kubernetes'}], 'projects':[{'header':'Fake project'}]}})
    text=nodes['#g-dropped-items']['textContent']
    assert 'Kubernetes' in text and 'Fake project' in text
    assert nodes['#g-cover']['textContent']=='Only letter'
