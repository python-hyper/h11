from cpython.bytes cimport PyBytes_FromStringAndSize, PyBytes_FromString

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
        http_cb      on_status_complete
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

    # Call this from on_headers_complete or on_message_complete
    # If it's 0, then this should be the last message on the connection
    # So server should say Connection: close, then send response, then close
    # Client should finish getting message, then close
    int http_should_keep_alive(const http_parser *parser)

    const char *http_method_str(unsigned char m)
    const char *http_errno_name(http_errno err)
    const char *http_errno_description(http_errno err)

cdef class State(object):
    cdef readonly list events
    # used to tell the parser that it should not expect a body, even if it
    # otherwise looks like one should be there (useful for HEAD)
    cdef readonly header_only
    # used to signal that we've just emitted the on-message-complete for an
    # upgraded connection, so we need to collect all unprocessed data and
    # expose it as an event
    cdef readonly just_upgraded
    def _add(self, *args, **kwargs):
        if kwargs:
            args = args + (kwargs,)
        self.events.append(args)

    def __cinit__(self):
        self.events = []
        self.header_only = False
        self.just_upgraded = False

cdef int on_message_begin(http_parser *p):
    (<State>p.data)._add("message-begin")
    return 0

cdef int on_url(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<State>p.data)._add("url-data", data)
    return 0

cdef int on_status_complete(http_parser *p):
    (<State>p.data)._add("status-complete")
    return 0

cdef int on_header_field(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<State>p.data)._add("header-field-data", data)
    return 0

cdef int on_header_value(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<State>p.data)._add("header-value-data", data)
    return 0

cdef int on_headers_complete(http_parser *p):
    cdef bytes method = PyBytes_FromString(http_method_str(p.method))
    (<State>p.data)._add("headers-complete",
                         http_major=p.http_major,
                         http_minor=p.http_minor,
                         status_code=p.status_code,
                         method=method,
                         should_keep_alive=http_should_keep_alive(p),
                         )
    # Special case in how libhttp_parser works: normally, returning non-zero
    # from a callback means "error, blow up". But for on_headers_complete, it
    # means "thanks, we don't expect anything beyond headers here" (maybe
    # because it's HEAD).
    #
    # The other magical thing is that (in very recent versions of
    # libhttp_parser) you can return 2 to mean "treat this as an
    # upgrade". (Just assigning to p->upgrade will also work, but is
    # considered ugly.) Upgrades are special in that not only do they make the
    # parser skip the body of the message, they make it actually stop early
    # and tell you where it stopped -- otherwise it continues on parsing the
    # next bit of data as the beginning of the next message. It tries to do
    # this by default for upgrades it can detect (CONNECT, Upgrade:,
    # Connection: upgrade) but you can also do it by hand.
    if (<State>p.data).header_only:
        return 1
    else:
        return 0

cdef int on_body(http_parser *p, const char *at, size_t length):
    cdef bytes data = PyBytes_FromStringAndSize(at, length)
    (<State>p.data)._add("body-data", data)
    return 0

cdef int on_message_complete(http_parser *p):
   (<State>p.data)._add("message-complete",
                        should_keep_alive=http_should_keep_alive(p))
   # reset this back to false after each item is processed
   (<State>p.data).header_only = False
   # check if this is the start of an upgrade, leaving HTTP behind
   if p.upgrade:
       (<State>p.data).just_upgraded = True
   return 0

class HttpParseError(RuntimeError):
    pass

cdef class HttpParser(object):
    cdef http_parser _parser
    cdef http_parser_settings _settings
    cdef State _state

    # read this, and call .clear() after doing so; .feed() just keeps
    # appending.
    property events:
        def __get__(self):
            return self._state.events

    # when you're about to process a HEAD response, set this to True. it
    # resets to False after the next request finishes.
    property header_only:
        def __get__(self):
            return self._state.header_only

        def __set__(self, value):
            self._state.header_only = value

    def __cinit__(self, mode):
        self._settings.on_message_begin = on_message_begin
        self._settings.on_url = on_url
        self._settings.on_status_complete = on_status_complete
        self._settings.on_header_field = on_header_field
        self._settings.on_header_value = on_header_value
        self._settings.on_headers_complete = on_headers_complete
        self._settings.on_body = on_body
        self._settings.on_message_complete = on_message_complete

        self._state = State()

        cdef http_parser_type type
        if mode == "both":
            type = HTTP_BOTH
        elif mode == "request":
            type = HTTP_REQUEST
        elif mode == "response":
            type = HTTP_RESPONSE
        else:
            raise ValueError("mode must be 'both', 'request', or 'response'")

        http_parser_init(&self._parser, type)
        self._parser.data = <void*>self._state

    def feed(self, data):
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if self._parser.http_errno != HPE_OK:
            # bug in caller
            raise RuntimeError("can't call feed() after error")
        if self._state.just_upgraded:
            # bug in caller
            raise RuntimeError("can't call feed() after upgrade")
        cdef int consumed = http_parser_execute(&self._parser,
                                                &self._settings,
                                                <char *>data,
                                                len(data))
        # there are two cases where consumed != len(data):
        # - there was an error, so how much we consumed is meaningless,
        #   because this isn't actually HTTP
        # - the connection is switching to a new protocol, so we need to get
        #   back the data that would have gone there.
        if self._parser.http_errno != HPE_OK:
            desc = http_errno_description(self._parser.http_errno)
            raise HttpParseError("http parse error: %s"
                                 % desc.decode("utf8"))
        if self._state.just_upgraded:
            self._state._add("upgraded", {"trailing-data": data[consumed:]})
        elif consumed != len(data):
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
