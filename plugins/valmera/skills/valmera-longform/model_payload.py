"""Losslessly intern repeated archive provenance in editorial requests."""
import json


def source_table(payload):
    records = {}
    ids = {}
    def visit(value):
        if isinstance(value, list):return [visit(v) for v in value]
        if not isinstance(value, dict):return value
        result = {}
        for key, item in value.items():
            if key == 'source' and isinstance(item, dict):
                serial = json.dumps(item, sort_keys=True)
                if serial not in ids:
                    name = f'source_{len(ids)}'
                    ids[serial] = name
                    records[name] = item
                result[key] = ids[serial]
            else:result[key] = visit(item)
        return result
    body = visit(payload)
    if not isinstance(body,dict) or {'source_records','reading_note'} & body.keys():
        raise ValueError('Expected an editorial request without reserved source-table keys')
    return dict(body,source_records=records,
                reading_note='Every source ID refers to its complete, unchanged source_records entry.')
