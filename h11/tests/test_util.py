import re
import sys
import traceback

import pytest

from .._util import *


def test_ProtocolError():
    with pytest.raises(TypeError):
        ProtocolError("abstract base class")


def test_LocalProtocolError():
    try:
        raise LocalProtocolError("foo")
    except LocalProtocolError as e:
        assert str(e) == "foo"
        assert e.error_status_hint == 400

    try:
        raise LocalProtocolError("foo", error_status_hint=418)
    except LocalProtocolError as e:
        assert str(e) == "foo"
        assert e.error_status_hint == 418

    def thunk():
        raise LocalProtocolError("a", error_status_hint=420)

    try:
        try:
            thunk()
        except LocalProtocolError as exc1:
            orig_traceback = "".join(traceback.format_tb(sys.exc_info()[2]))
            exc1._reraise_as_remote_protocol_error()
    except RemoteProtocolError as exc2:
        assert type(exc2) is RemoteProtocolError
        assert exc2.args == ("a",)
        assert exc2.error_status_hint == 420
        new_traceback = "".join(traceback.format_tb(sys.exc_info()[2]))
        assert new_traceback.endswith(orig_traceback)


def test_make_sentinel():
    S = make_sentinel("S")
    assert repr(S) == "S"
    assert S == S
    assert type(S).__name__ == "S"
    assert S in {S}
    assert type(S) is S
    S2 = make_sentinel("S2")
    assert repr(S2) == "S2"
    assert S != S2
    assert S not in {S2}
    assert type(S) is not type(S2)


def test_bytesify():
    assert bytesify(b"123") == b"123"
    assert bytesify(bytearray(b"123")) == b"123"
    assert bytesify("123") == b"123"

    with pytest.raises(UnicodeEncodeError):
        bytesify("\u1234")

    with pytest.raises(TypeError):
        bytesify(10)
