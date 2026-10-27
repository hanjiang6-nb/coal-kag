import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from scripts.visualize_knowledge_graph import render


class VisualizationTests(unittest.TestCase):
    def test_offline_interactions_and_safe_embedding(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            nodes=[dict(id='scope',kind='scope',document_id='paper',passage='evidence </script>'),
                   dict(id='a',kind='entity_mention',layer='binary',annotation=dict(text='Coal',type='rock')),
                   dict(id='b',kind='entity_mention',layer='binary',annotation=dict(text='Basin',type='basin')),
                   dict(id='r',kind='relations',scope='scope',layer='binary',predicate='located in',default_fact_eligible=True,annotation=dict(modality='asserted'))]
            edges=[dict(source='r',target='a',type='HEAD'),dict(source='r',target='b',type='TAIL')]
            for name,rows in [('nodes',nodes),('edges',edges)]:
                (root/(name+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows),encoding='utf-8')
            render(root,root/'view.html')
            html=(root/'view.html').read_text(encoding='utf-8')
            payload=re.search(r'type="application/json">(.*?)</script>',html,re.S)[1]
            self.assertNotIn('</script>',payload)
            script=re.findall(r'<script>(.*?)</script>',html,re.S)[0]
            stub='''const vm=require('vm');const payload=PAYLOAD;const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:id==='layer'?'facts':'all',textContent:'',appendChild(){},replaceChildren(){},addEventListener(){},getBoundingClientRect(){return {width:900,height:600,left:0,top:0}},getContext(){return new Proxy({},{get(){return ()=>{}}})},setPointerCapture(){}});return elements.get(id)}
element('search').value='';element('graph-data').textContent=payload;
global.document={getElementById:element,createElement:()=>({})};global.window={};global.devicePixelRatio=1;global.requestAnimationFrame=()=>{};
vm.runInThisContext(SCRIPT);
if(!element('count').textContent.includes('1 条关系'))throw Error('initial filter');
element('search').value='missing';vm.runInThisContext('filter()');if(!element('count').textContent.includes('0 条关系'))throw Error('search');
element('search').value='Coal';vm.runInThisContext('filter();showNode(visible[0]);showEdge(links[0])');if(!element('details').textContent.includes('evidence </script>'))throw Error('evidence');
console.log('PASS offline graph interactions');'''
            code=stub.replace('PAYLOAD',json.dumps(payload)).replace('SCRIPT',json.dumps(script))
            (root/'test.js').write_text(code,encoding='utf-8')
            result=subprocess.run(['node',str(root/'test.js')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
