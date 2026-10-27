import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts.cluster_two_stage_ontologies import report
from ontology_report_zh import display_metadata


class ChineseReportTests(unittest.TestCase):
    def test_display_translation_preserves_original_members_weights_and_formula(self):
        profiles = [dict(entity='a', name='Kaolinite'),
                    dict(entity='b', name='kaolinite'),
                    dict(entity='c', name='Eocene')]
        ontologies = [dict(id='o', members=['a', 'b'], stage='attributes',
                           label='属性本体：Kaolinite / kaolinite')]
        feature_details = {'formula-id': dict(key='formula', value='Al2Si2O5(OH)4')}
        before = copy.deepcopy((profiles, ontologies, feature_details))
        display = display_metadata(profiles, ontologies, feature_details)
        self.assertEqual(display['display_names']['a'], '高岭石')
        self.assertEqual(display['display_names']['b'], '高岭石')
        self.assertEqual(display['display_names']['c'], '始新世')
        self.assertEqual(display['display_names']['o'], '属性本体：高岭石')
        self.assertIn('Al2Si2O5(OH)4', display['display_names']['formula-id'])
        self.assertEqual((profiles, ontologies, feature_details), before)

        boundary = [dict(entity='c', ontology='o', round=1, weight=3,
                         connection_ids=['r1', 'r2', 'r3'])]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'report.html'
            report(profiles, ontologies, [], boundary, [], {'relation_rounds': 1}, path,
                   {'r1': dict(annotation=dict(evidence_text='</script><script>original'))},
                   feature_details)
            html = path.read_text(encoding='utf-8')
            payload = json.loads(re.search(r'type="application/json">(.*?)</script>', html, re.S)[1])
            self.assertEqual(payload['profiles'], profiles)
            self.assertEqual(payload['anchors'], ontologies)
            self.assertEqual(payload['boundary'], boundary)
            self.assertEqual(payload['connections']['r1']['annotation']['evidence_text'],
                             '</script><script>original')
            self.assertNotIn('</script><script>original', html)
            self.assertIn('中文说明', html)
            self.assertIn('原始数据', html)
            script = re.findall(r'<script>(.*?)</script>', html, re.S)[0]
            code = '''
const vm=require('vm'),elements=new Map();
function node(){return {value:'',textContent:'',children:[],appendChild(n){this.children.push(n)},replaceChildren(...n){this.children=n},setAttribute(){}}}
function get(id){if(!elements.has(id))elements.set(id,node());return elements.get(id)}
global.document={getElementById:get,createElement:node,createElementNS:node,createTextNode:text=>({textContent:text})};
get('mode').value='attributes';get('graphRound').value='1';get('data').textContent=PAYLOAD;
vm.runInThisContext(SCRIPT);
if(!get('list').children[0].textContent.includes('高岭石'))throw Error('Chinese name missing');
for(const query of ['高岭石','Kaolinite']){get('search').value=query;vm.runInThisContext('filter()');if(get('list').children.length!==1)throw Error('bilingual search failed')}
get('search').value='';get('mode').value='weights';vm.runInThisContext('filter()');
if(!get('list').children[0].textContent.includes('始新世'))throw Error('Chinese entity missing');
get('list').children[0].onclick();
if(!get('detail').textContent.includes('原始关系编号'))throw Error('Chinese fields missing');
if(!get('detail').textContent.includes('</script><script>original'))throw Error('original evidence changed');
get('detailMode').value='raw';get('detailMode').onchange();
const raw=JSON.parse(get('detail').textContent);
if(raw.weight!==3||raw.entity!=='c'||raw.connection_ids.join(',')!=='r1,r2,r3')throw Error('raw data changed');
'''
            code = code.replace('PAYLOAD', json.dumps(json.dumps(payload, ensure_ascii=False)))
            code = code.replace('SCRIPT', json.dumps(script, ensure_ascii=False))
            test_script = Path(temp) / 'chinese_ui.js'
            test_script.write_text(code, encoding='utf-8')
            result = subprocess.run(['node', str(test_script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
