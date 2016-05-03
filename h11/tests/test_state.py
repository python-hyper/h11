import pytest

from ..util import ProtocolError
from ..events import *
from ..state import *
from ..state import ConnectionState

def test_ConnectionState():
    cs = ConnectionState()

    assert cs.states == {CLIENT: IDLE, SERVER: IDLE}

    cs.process_event(CLIENT, Request, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    with pytest.raises(ProtocolError):
        cs.process_event(CLIENT, Request, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    cs.process_event(SERVER, InformationalResponse, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    # XX state-triggered and special cases

def test_ConnectionState_reuse():
    cs = ConnectionState()

    assert cs.can_reuse == "maybe-later"

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    cs.process_event(CLIENT, Request, False)
    cs.process_event(CLIENT, EndOfMessage, False)
    assert cs.can_reuse == "maybe-later"

    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)
    assert cs.can_reuse == "now"

    cs.prepare_to_reuse()
    assert cs.states == {CLIENT: IDLE, SERVER: IDLE}

    assert cs.can_reuse == "maybe-later"

    # No keepalive

    cs.process_event(CLIENT, Request, False)
    cs.keep_alive = False
    assert cs.can_reuse == "never"

    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)
    assert cs.can_reuse == "never"

    # One side closed

    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(CLIENT, ConnectionClosed, False)
    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)
    assert cs.can_reuse == "never"

    # XX protocol switch
