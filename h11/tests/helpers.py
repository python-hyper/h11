from ..events import *

# Merges adjacent Data events, and converts payloads to bytestrings
def normalize_data_events(in_events):
    out_events = []
    for event in in_events:
        if type(event) is Data:
            event.data = bytes(event.data)
        if out_events and type(out_events[-1]) is type(event) is Data:
            out_events[-1].data += event.data
        else:
            out_events.append(event)
    return out_events

