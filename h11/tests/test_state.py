import pytest

from ..util import ProtocolError
from ..events import *
from ..state import *
from ..state import ConnectionState

def test_ConnectionState():
    cs = ConnectionState()

    # Basic event-triggered transitions

    assert cs.states == {CLIENT: IDLE, SERVER: IDLE}

    cs.process_event(CLIENT, Request, False)
    # The SERVER-Request special case:
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    # Illegal transitions raise an error and nothing happens
    with pytest.raises(ProtocolError):
        cs.process_event(CLIENT, Request, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    cs.process_event(SERVER, InformationalResponse, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    cs.process_event(SERVER, Response, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_BODY}

    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(SERVER, EndOfMessage, False)
    assert cs.states == {CLIENT: DONE, SERVER: DONE}

    # State-triggered transition

    cs.process_event(SERVER, ConnectionClosed, False)
    assert cs.states == {CLIENT: MUST_CLOSE, SERVER: CLOSED}

def test_ConnectionState_keep_alive():
    # keep_alive = False
    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.set_keep_alive_disabled()
    cs.process_event(CLIENT, EndOfMessage, False)
    assert cs.states == {CLIENT: MUST_CLOSE, SERVER: SEND_RESPONSE}

    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)
    assert cs.states == {CLIENT: MUST_CLOSE, SERVER: MUST_CLOSE}

def test_ConnectionState_keep_alive_in_DONE():
    # Check that if keep_alive is disabled when the CLIENT is already in DONE,
    # then this is sufficient to immediately trigger the DONE -> MUST_CLOSE
    # transition
    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.process_event(CLIENT, EndOfMessage, False)
    assert cs.states[CLIENT] is DONE
    cs.set_keep_alive_disabled()
    assert cs.states[CLIENT] is MUST_CLOSE

def test_ConnectionState_protocol_switch_denied():
    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.set_client_requested_protocol_switch()
    cs.process_event(CLIENT, Data, False)
    assert cs.states == {CLIENT: SEND_BODY, SERVER: SEND_RESPONSE}

    cs.process_event(CLIENT, EndOfMessage, False)
    assert cs.states == {CLIENT: MIGHT_SWITCH_PROTOCOL, SERVER: SEND_RESPONSE}

    assert not cs.client_requested_protocol_switch_pending

    cs.process_event(SERVER, InformationalResponse, False)
    assert cs.states == {CLIENT: MIGHT_SWITCH_PROTOCOL, SERVER: SEND_RESPONSE}

    cs.process_event(SERVER, Response, False)
    assert cs.states == {CLIENT: DONE, SERVER: SEND_BODY}

def test_ConnectionState_protocol_switch_accepted():
    for accept_type in (InformationalResponse, Response):
        cs = ConnectionState()
        cs.process_event(CLIENT, Request, False)
        cs.set_client_requested_protocol_switch()
        cs.process_event(CLIENT, Data, False)
        assert cs.states == {CLIENT: SEND_BODY,
                             SERVER: SEND_RESPONSE}

        cs.process_event(CLIENT, EndOfMessage, False)
        assert cs.states == {CLIENT: MIGHT_SWITCH_PROTOCOL,
                             SERVER: SEND_RESPONSE}

        assert not cs.client_requested_protocol_switch_pending

        cs.process_event(SERVER, InformationalResponse, False)
        assert cs.states == {CLIENT: MIGHT_SWITCH_PROTOCOL,
                             SERVER: SEND_RESPONSE}

        cs.process_event(SERVER, accept_type, True)
        assert cs.states == {CLIENT: SWITCHED_PROTOCOL,
                             SERVER: SWITCHED_PROTOCOL}

def test_ConnectionState_keepalive_protocol_switch_interaction():
    # keep_alive = False + client_requested_protocol_switch_pending = True
    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.set_keep_alive_disabled()
    cs.set_client_requested_protocol_switch()
    cs.process_event(CLIENT, Data, False)
    assert cs.states == {CLIENT: SEND_BODY,
                         SERVER: SEND_RESPONSE}

    # the protocol switch "wins"
    cs.process_event(CLIENT, EndOfMessage, False)
    assert cs.states == {CLIENT: MIGHT_SWITCH_PROTOCOL,
                         SERVER: SEND_RESPONSE}

    assert not cs.client_requested_protocol_switch_pending
    assert not cs.keep_alive

    # but when the server denies the request, keep_alive comes back into play
    cs.process_event(SERVER, Response, False)
    assert cs.states == {CLIENT: MUST_CLOSE, SERVER: SEND_BODY}


def test_ConnectionState_reuse():
    cs = ConnectionState()

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    cs.process_event(CLIENT, Request, False)
    cs.process_event(CLIENT, EndOfMessage, False)

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)

    cs.prepare_to_reuse()
    assert cs.states == {CLIENT: IDLE, SERVER: IDLE}

    # No keepalive

    cs.process_event(CLIENT, Request, False)
    cs.set_keep_alive_disabled()
    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    # One side closed

    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(CLIENT, ConnectionClosed, False)
    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    # Succesful protocol switch

    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.set_client_requested_protocol_switch()
    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(SERVER, Response, True)

    with pytest.raises(ProtocolError):
        cs.prepare_to_reuse()

    # Failed protocol switch

    cs = ConnectionState()
    cs.process_event(CLIENT, Request, False)
    cs.set_client_requested_protocol_switch()
    cs.process_event(CLIENT, EndOfMessage, False)
    cs.process_event(SERVER, Response, False)
    cs.process_event(SERVER, EndOfMessage, False)

    cs.prepare_to_reuse()
    assert cs.states == {CLIENT: IDLE, SERVER: IDLE}

def test_server_request_is_illegal():
    # There used to be a bug in how we handled the Request special case that
    # made this allowed...
    cs = ConnectionState()
    with pytest.raises(ProtocolError):
        cs.process_event(SERVER, Request, False)
