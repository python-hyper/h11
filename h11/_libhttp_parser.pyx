from cpython.bytes cimport PyBytes_FromStringAndSize, PyBytes_FromString

__all__ = ["HttpParser", "HttpParseError"]

cdef extern from "http_parser.h":
    enum http_parser_type:
        HTTP_REQUEST
        HTTP_RESPONSE
        HTTP_BOTH

    enum http_errno:
        HPE_OK

    ctypedef struct http_parser:
        int http_major
        int http_minor
        int status_code       # responses only
        unsigned char method  # requests only
        http_errno http_errno
        unsigned char upgrade
        void * data

    ctypedef int (*http_data_cb)(http_parser *, const char *at, size_t length)
    ctypedef int (*http_cb)(http_parser *)
    ctypedef struct http_parser_settings:
        http_cb      on_message_begin
        http_data_cb on_url
        # There is also an on_status callback, but it has different names in
        # 2.1 and 2.7, and in neither case does it appear to be called under
        # any circumstances.
        http_data_cb on_header_field
        http_data_cb on_header_value
        # this is when status_code, method, http version are valid
        http_cb      on_headers_complete
        http_data_cb on_body
        http_cb      on_message_complete

    void http_parser_init(http_parser *parser, http_parser_type type)
    # Returns the number of bytes consumed
    #   (might be < len if e.g. upgrade happened)
    # Pass in len==0 to tell it about EOF
    size_t http_parser_execute(http_parser *parser,
                               const http_parser_settings *settings,
                               const char * data,
                               size_t len)

    const char *http_method_str(unsigned char m)
    const char *http_errno_name(http_errno err)
    const char *http_errno_description(http_errno err)

cdef class InternalState(object):
    cdef list events
    cdef object client_side

    # On the client side, we need to know if we're processing the response to
    # a HEAD or CONNECT, because they have special rules for handling the
    # response body. We also pass this through in our headers-complete event
    # so downstream processors have access to it too.
    cdef object request_method

    def __cinit__(self, *, client_side):
        self.events = []
        self.client_side = client_side
        # clients should set this to keep track of what they requested, so
        # we can properly interpret the response
        self.request_method = None

    def _add(self, *args, **kwargs):
        if kwargs:
            args = args + (kwargs,)
        self.events.append(args)

    def set_request_method(self, method):
        if not self.client_side:
            raise RuntimeError(
                "only clients should call set_request_method")
        self.request_method = method

cdef int on_message_begin(http_parser *p):
    (<InternalState>p.data)._add("message-begin")
    return 0

cdef int on_url(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<InternalState>p.data)._add("url-data", data)
    return 0

# cdef int on_status_complete(http_parser *p):
#     (<InternalState>p.data)._add("status-complete")
#     return 0

cdef int on_header_field(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<InternalState>p.data)._add("header-field-data", data)
    return 0

cdef int on_header_value(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<InternalState>p.data)._add("header-value-data", data)
    return 0

cdef int on_headers_complete(http_parser *p):
    if (<InternalState>p.data).client_side:
        kwargs = {"status_code": p.status_code}
    else:
        method = PyBytes_FromString(http_method_str(p.method))
        kwargs = {"method": method}
    # This is safe, b/c HTTP versions are spec'ed to be 1 digit max
    http_version="%s.%s" % (p.http_major, p.http_minor),
    (<InternalState>p.data)._add("headers-complete",
                                 http_version=http_version,
                                 **kwargs)
    # Special case in how libhttp_parser works: normally, returning non-zero
    # from a callback means "error, blow up". But for on_headers_complete,
    # there are some magic return values available that change body handling.
    #
    # Rules for determining whether there's a body are at:
    #   https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#rfc.section.3.3.p.4
    # Basically there are two cases where you can't tell without some external
    # information: responses to HEAD and responses to CONNECT.
    if (<InternalState>p.data).client_side:
        if (<InternalState>p.data).request_method == b"HEAD":
            # HEAD responses have no body. 1 is the magic value meaning "no
            # body (but otherwise continue processing as normal)"
            return 1
        elif (<InternalState>p.data).request_method == b"CONNECT":
            # Successful 2xx CONNECT responses have no body -- instead, the
            # connection hands off to the proxied connection after the end of
            # the headers, basically an upgrade. 2 is the magic value meaning
            # "treat this as an upgrade".
            # NB: this requires a very recent version of libhttp_parser
            # (~2.7 or better, not yet released as of 2016-04-25)
            if 200 <= p.status_code < 300:
                return 2
    if p.status_code >= 200:
        (<InternalState>p.data).request_method = None
    return 0

cdef int on_body(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<InternalState>p.data)._add("body-data", data)
    return 0

cdef int on_message_complete(http_parser *p):
   # reset this back to false after each item is processed
   (<InternalState>p.data).header_only = False
   # This will have upgrade information added in feed()
   (<InternalState>p.data)._add("message-complete", upgrade=p.upgrade)
   return 0

class HttpParseError(RuntimeError):
    pass

cdef class LowlevelHttpParser(object):
    cdef http_parser _parser
    cdef http_parser_settings _settings
    # exposed to python to ease debugging -- but these are still internal
    # implementation details.
    cdef public InternalState _state

    # read this, and call .clear() after doing so; .feed() just keeps
    # appending.
    property events:
        def __get__(self):
            return self._state.events

    def __cinit__(self, *, client_side):
        self._settings.on_message_begin = on_message_begin
        self._settings.on_url = on_url
        self._settings.on_header_field = on_header_field
        self._settings.on_header_value = on_header_value
        self._settings.on_headers_complete = on_headers_complete
        self._settings.on_body = on_body
        self._settings.on_message_complete = on_message_complete

        self._state = InternalState(client_side=client_side)

        cdef http_parser_type type
        if client_side:
            type = HTTP_RESPONSE
        else:
            type = HTTP_REQUEST

        http_parser_init(&self._parser, type)
        self._parser.data = <void*>self._state

    def set_request_method(self, method):
        self._state.set_request_method(method)

    def feed(self, data):
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray")

        if self._parser.http_errno != HPE_OK:
            # bug in caller
            raise RuntimeError("can't call feed() after error")

        cdef int consumed = http_parser_execute(&self._parser,
                                                &self._settings,
                                                <char *>data,
                                                len(data))

        # there are two cases where consumed != len(data):
        # - there was an error, so how much we consumed is meaningless,
        #   because this isn't actually HTTP
        # - the connection is switching to a new protocol, so we need to give
        #   back the trailing data, which is part of the new protocol's
        #   chatter
        if self._parser.http_errno != HPE_OK:
            desc = http_errno_description(self._parser.http_errno)
            raise HttpParseError("http parse error: %s"
                                 % desc.decode("utf8"))

        # Special case: after a message-complete, stash
        # the unconsumed bytes directly in the message-complete event (useful
        # for upgrade requests)
        if self._state.events[-1][0] == "message-complete":
            payload = self._state.events[-1][1]
            payload["trailing_data"] = data[consumed:]

        if consumed != len(data) and not self._parser.upgrade:
            raise RuntimeError("bug in _http_parser.pyx")

# How http-parser works, based on empirical observations:
#
# We receive, in this order:
#   message-begin
#   on-url (multiple fragments)
#   on-header-field / on-header-value (multiple fragments)
#   on-headers-complete (also gives http version, method, status code)
#     (method for request, status for response)
#   on-body (multiple fragments)
#   on-header-field / on-header-value (multiple fragments) for trailers
#   on-message-complete
# and then if we keep feeding it data it just keeps going. There's no way to
# tell it to process exactly 1 message and then stop.
#
# But, if an upgrade happens (CONNECT, Upgrade: ..., Connection: upgrade, ...)
# then it will stop at the end of the message and tell you where it stopped,
# so that you can pull out the trailing data that's the beginning of the next
# thing.
#
# Other notes:
# - probably should require setting request vs. response and then only give
#   the relevant parts of method/status_code
# - the should_keep_alive flag encapsulates the HTTP/1.0 vs HTTP/1.1,
#   Connection: close vs Connection: keep-alive logic
