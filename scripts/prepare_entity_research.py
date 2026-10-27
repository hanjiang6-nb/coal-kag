"""Inventory every entity and prepare reproducible external research queries."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
from scripts.build_knowledge_graph import identifier, load, SOURCES


def prepare(source, output):
    output.mkdir(parents=True, exist_ok=False)
    inventory = {}
    for filename, layer in SOURCES:
        for record in load(source / filename):
            for entity in record['annotation']['entities']:
                key = identifier(entity['type'], entity['text'].strip().casefold())
                item = inventory.setdefault(key, dict(id=key, name=entity['text'], type=entity['type'], mentions=[]))
                item['mentions'].append(dict(entity_id=entity['id'], scope_id=record['task']['id'],
                    layer=layer, document_id=record['annotation']['paper_id'],
                    passage=record['input']['PASSAGE']))
    queries = []
    for item in inventory.values():
        name = re.sub(r'["\r\n]', ' ', item['name'])
        category = item['type']
        context = item['mentions'][0]['passage']
        if category == 'chemical element':
            query = 'site:pubchem.ncbi.nlm.nih.gov/element/ "' + name + '" element properties'
        elif category == 'mineral':
            query = '"' + name + '" mineral crystal morphology hardness cleavage'
        elif category in {'sample or material', 'coal seam', 'geological body', 'stratigraphic unit'}:
            query = '"' + name + '" ' + category + ' ' + ' '.join(context.split()[:16])
        else:
            query = '"' + name + '" ' + category + ' geological properties research'
        item['query'] = query
        item['search_status'] = 'queued'
        queries.append(dict(entity_id=item['id'], query=query))
    (output / 'inventory.json').write_text(json.dumps(list(inventory.values()),ensure_ascii=False,indent=2),encoding='utf-8')
    (output / 'search_batches.json').write_text(json.dumps([queries[i:i+4] for i in range(0,len(queries),4)],ensure_ascii=False,indent=2),encoding='utf-8')
    summary = dict(unique_name_type_pairs=len(inventory),mentions=sum(len(i['mentions']) for i in inventory.values()),
                   by_type=dict(Counter(i['type'] for i in inventory.values())),
                   meaning='Name/type groups are research units, not confirmed cross-document identities.')
    (output/'inventory_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path('data'));parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(prepare(args.source,args.output)))
