"""Bounded data-only JSON framing for the trusted numerical worker."""
import json
import math

PAYLOAD_BYTES = 1024 * 1024

def _encode(message):
    remaining = PAYLOAD_BYTES
    def check(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ValueError('IPC payload size or nesting limit')
        kind = type(value)
        if kind is str:
            remaining -= len(value.encode('utf-8'))
        elif kind in (list, tuple):
            for item in value:
                check(item, depth + 1)
        elif kind is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError('IPC keys must be strings')
                check(key, depth + 1)
                check(item, depth + 1)
        elif kind in (int, float):
            if not math.isfinite(value):
                raise ValueError('IPC numbers must be finite')
        elif value is not None and kind is not bool:
            raise ValueError('IPC accepts plain JSON only')
        if remaining < 0:
            raise ValueError('IPC payload size limit')
    check(message)
    data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode()
    if len(data) > PAYLOAD_BYTES:
        raise ValueError('IPC payload size limit')
    return data + b'\n'

def _decode(data):
    if len(data) > PAYLOAD_BYTES + 1:
        raise ValueError('IPC payload size limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON')
    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite JSON')
        return number
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
