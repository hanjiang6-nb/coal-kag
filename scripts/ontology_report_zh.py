"""Chinese display metadata; original clustering objects remain untouched."""
from graph_zh_glossary import LABELS, translate_name, translate_term
from graph_zh_geology import GEOLOGY_NAMES


FIELD_LABELS = {
    'entity': '实体', 'name': '名称', 'type': '类型', 'id': '编号',
    'identity_level': '身份层级', 'intrinsic_attributes': '天然属性',
    'attribute_eligible': '可参与属性聚类', 'property_evidence': '属性证据',
    'reference_derivations': '参考属性推导', 'reference': '参考概念',
    'feature_values': '特征值', 'interpretation': '解释', 'members': '成员',
    'stage': '生成阶段', 'ontology_frozen': '本体已冻结', 'label': '名称',
    'member_type': '成员类型', 'member_identity_level': '成员身份层级',
    'shared_attributes': '共享属性', 'minimum_similarity': '最小相似度',
    'formal_ontology_axiom': '已形成正式本体公理', 'round': '轮次',
    'boundary_profiles': '各成员到冻结本体的关系权重', 'ontology': '本体',
    'weight': '关系权重', 'connection_ids': '原始关系编号',
    'reason': '未聚类原因', 'intrinsic_feature_count': '天然属性数量',
    'boundary_weights': '到冻结本体的关系权重', 'connections': '原始关系证据',
    'predicate': '关系', 'annotation': '原始标注', 'head': '头实体原文编号',
    'tail': '尾实体原文编号', 'modality': '陈述状态',
    'evidence_text': '原文证据', 'evidence_sentence_ids': '原文句子编号',
    'source': '来源本体', 'target': '目标本体', 'records': '连线明细',
    'display_only': '仅供展示', 'source_payload': '既有本体原始内容',
    'kind': '节点类别', 'feature': '特征', 'key': '特征名称', 'value': '特征值',
    'unit': '单位', 'original_name': '原文名称',
}

STATUS_LABELS = {
    'attributes': '天然属性聚类', 'relations': '关系聚类', 'existing': '已有本体',
    'source_mention': '文献中的实体提及', 'reference_kind': '物种参考概念',
    'kind_reference_for_clustering_not_specimen_measurement':
        '物种参考属性，仅用于聚类，非样品实测值',
    'no_connection_to_frozen_ontologies': '与冻结本体暂无已确认的关系连线',
    'no_supported_relation_peer': '暂无满足关系相似度要求的其他实体',
    'asserted': '已断言', 'ontology': '本体', 'entity_mention': '实体提及',
    'reference_concept': '参考概念',
}


def display_metadata(profiles, ontologies, feature_details=None):
    def translated(text):
        return GEOLOGY_NAMES.get(text, GEOLOGY_NAMES.get(text.lower(), translate_name(text)))

    names = {p['entity']: ('第三系' if p['name'].casefold() == 'tertiary'
                          and p.get('type') == 'stratigraphic unit' else translated(p['name']))
             for p in profiles}
    for ontology in ontologies:
        member_names = sorted({names[k] for k in ontology['members'] if k in names})
        if member_names:
            prefix = {'attributes': '属性本体', 'relations': '关系本体',
                      'existing': '已有本体'}.get(ontology['stage'], '本体')
            names[ontology['id']] = prefix + '：' + ' / '.join(member_names)
        else:
            names[ontology['id']] = ontology['label']
    for key, feature in (feature_details or {}).items():
        value = feature.get('value')
        text = str(value) if feature['key'] == 'formula' else translate_term(str(value))
        if value == 'cubic' and feature['key'] in {'crystal_family', 'habit'}:
            text = '等轴晶族' if feature['key'] == 'crystal_family' else '立方体状'
        names[key] = translate_term(feature['key']) + (
            '：' + text if value is not None else '')
    terms = dict(LABELS)
    terms.update(STATUS_LABELS)
    for p in profiles:
        terms.setdefault(p['name'], names[p['entity']])
    for ontology in ontologies:
        terms[ontology['label']] = names[ontology['id']]
    return dict(display_names=names, field_labels=FIELD_LABELS, term_labels=terms)
