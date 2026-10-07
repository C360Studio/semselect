"""One caller-owned literal binder shared by every evaluation arm.

Catalog membership establishes neither authorization nor entity existence.
Partial options are diagnostic hints only: needs_binding is never executable.
"""
from __future__ import annotations

import re

OPERATIONS = ('similarity', 'path', 'zone', 'count', 'avg', 'sum', 'min', 'max', 'no_override')


def bind(query: str, operation: str, catalog: dict) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 8192:
        raise ValueError('query must be nonempty and at most 8192 bytes')
    if operation not in OPERATIONS:
        raise ValueError('unknown operation')
    if not isinstance(catalog, dict) or set(catalog) != {'nodes', 'zones', 'fields'}:
        raise ValueError('catalog requires nodes, zones, fields')
    for values in catalog.values():
        if (not isinstance(values, list) or len(values) > 256
                or any(not isinstance(v, str) or not v or len(v.encode()) > 128 for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('catalog entries must be unique nonempty bounded strings')
    if operation == 'no_override':
        return {'readiness': 'no_override', 'options': {}, 'executable': False}
    if operation == 'similarity':
        options = {'use_embeddings': True}
    elif operation in ('path', 'zone'):
        options = {'path_intent': True}
        if operation == 'zone':
            options['path_predicates'] = ['located_in']
    else:
        options = {'aggregation_type': operation}
    if operation in ('path', 'zone', 'avg', 'sum', 'min', 'max'):
        key = 'nodes' if operation == 'path' else 'zones' if operation == 'zone' else 'fields'
        # Case-sensitive, whole identifiers; dotted suffixes remain ID characters,
        # while a terminal sentence period is punctuation.
        # Quoted literals count, repeated occurrences count once, order confers no priority.
        matches = [v for v in catalog[key]
                   if re.search(r'(?<![\w./:-])' + re.escape(v) + r'(?![\w/:-]|\.\w)', query)]
        if len(matches) != 1:
            return {'readiness': 'needs_binding', 'options': options, 'executable': False}
        options['path_start_node' if operation in ('path', 'zone') else 'aggregation_field'] = matches[0]
    return {'readiness': 'valid_plan', 'options': options, 'executable': True}
