import pytest

from .._util import LocalProtocolError
from .. import _events
from .._events import *

def test_event_bundle():
    class T(_events._EventBundle):
        _fields = ["a", "b"]
        _defaults = {"b": 1}

        def _validate(self):
            if self.a == 0:
                raise ValueError

    # basic construction and methods
    t = T(a=1, b=0)
    assert repr(t) == "T(a=1, b=0)"
    assert t == T(a=1, b=0)
    assert not (t == T(a=2, b=0))
    assert not (t != T(a=1, b=0))
    assert (t != T(a=2, b=0))
    with pytest.raises(TypeError):
        hash(t)

    # check defaults
    t = T(a=10)
    assert t.a == 10
    assert t.b == 1

    # no positional args
    with pytest.raises(TypeError):
        T(1)

    with pytest.raises(TypeError):
        T(1, a=1, b=0)

    # unknown field
    with pytest.raises(TypeError):
        T(a=1, b=0, c=10)

    # missing required field
    with pytest.raises(TypeError) as exc:
        T(b=0)
    # make sure we error on the right missing kwarg
    assert 'kwarg a' in str(exc)

    # _validate is called
    with pytest.raises(ValueError):
        T(a=0, b=0)

def test_events():
    with pytest.raises(LocalProtocolError):
        # Missing Host:
        req = Request(method="GET", target="/", headers=[("a", "b")],
                      http_version="1.1")
    # But this is okay (HTTP/1.0)
    req = Request(method="GET", target="/", headers=[("a", "b")],
                  http_version="1.0")
    # fields are normalized
    assert req.method == b"GET"
    assert req.target == b"/"
    assert req.headers == [(b"a", b"b")]
    assert req.http_version == b"1.0"

    # This is also okay -- has a Host (with weird capitalization, which is ok)
    req = Request(method="GET", target="/",
                  headers=[("a", "b"), ("hOSt", "example.com")],
                  http_version="1.1")
    # we normalize header capitalization
    assert req.headers == [(b"a", b"b"), (b"host", b"example.com")]

    # Multiple host is bad too
    with pytest.raises(LocalProtocolError):
        req = Request(method="GET", target="/",
                      headers=[("Host", "a"), ("Host", "a")],
                      http_version="1.1")
    # Even for HTTP/1.0
    with pytest.raises(LocalProtocolError):
        req = Request(method="GET", target="/",
                      headers=[("Host", "a"), ("Host", "a")],
                      http_version="1.0")

    # Header values are validated
    with pytest.raises(LocalProtocolError):
        req = Request(method="GET", target="/",
                      headers=[("Host", "a"), ("Foo", "  asd\x00")],
                      http_version="1.0")

    ir = InformationalResponse(status_code=100, headers=[("Host", "a")])
    assert ir.status_code == 100
    assert ir.headers == [(b"host", b"a")]
    assert ir.http_version == b"1.1"

    with pytest.raises(LocalProtocolError):
        InformationalResponse(status_code=200, headers=[("Host", "a")])

    resp = Response(status_code=204, headers=[], http_version="1.0")
    assert resp.status_code == 204
    assert resp.headers == []
    assert resp.http_version == b"1.0"

    with pytest.raises(LocalProtocolError):
        resp = Response(status_code=100, headers=[], http_version="1.0")

    with pytest.raises(LocalProtocolError):
        Response(status_code="100", headers=[], http_version="1.0")

    with pytest.raises(LocalProtocolError):
        InformationalResponse(status_code=b"100",
                              headers=[], http_version="1.0")

    d = Data(data=b"asdf")
    assert d.data == b"asdf"

    eom = EndOfMessage()
    assert eom.headers == []

    cc = ConnectionClosed()
    assert repr(cc) == "ConnectionClosed()"
