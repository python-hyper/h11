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

def test_normalize_data_events():
    assert (normalize_data_events(
        [Data(data=bytearray(b"1")), Data(data=b"2"),
         Response(status_code=200, headers=[]),
         Data(data=b"3"), Data(data=b"4"),
         EndOfMessage(),
         Data(data=b"5"), Data(data=b"6"), Data(data=b"7")]
        ) == [
            Data(data=b"12"),
            Response(status_code=200, headers=[]),
            Data(data=b"34"),
            EndOfMessage(),
            Data(data=b"567"),
            ])
