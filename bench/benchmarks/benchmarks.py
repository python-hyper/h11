# Write the benchmarking functions here.
# See "Writing benchmarks" in the asv docs for more information.

import h11


# Basic ASV benchmark of core functionality
def time_server_basic_get_with_realistic_headers():
    c = h11.Connection(h11.SERVER)
    c.receive_data(
        b"GET / HTTP/1.1\r\n"
        b"Host: example.com\r\n"
        b"User-Agent: Mozilla/5.0 (X11; Linux x86_64; "
        b"rv:45.0) Gecko/20100101 Firefox/45.0\r\n"
        b"Accept: text/html,application/xhtml+xml,"
        b"application/xml;q=0.9,*/*;q=0.8\r\n"
        b"Accept-Language: en-US,en;q=0.5\r\n"
        b"Accept-Encoding: gzip, deflate, br\r\n"
        b"DNT: 1\r\n"
        b"Cookie: ID=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\r\n"
        b"Connection: keep-alive\r\n\r\n"
    )
    while True:
        event = c.next_event()
        if event is h11.NEED_DATA:
            break

    c.send(
        h11.Response(
            status_code=200,
            headers=[
                (b"Cache-Control", b"private, max-age=0"),
                (b"Content-Encoding", b"gzip"),
                (b"Content-Type", b"text/html; charset=UTF-8"),
                (b"Date", b"Fri, 20 May 2016 09:23:41 GMT"),
                (b"Expires", b"-1"),
                (b"Server", b"gws"),
                (b"X-Frame-Options", b"SAMEORIGIN"),
                (b"X-XSS-Protection", b"1; mode=block"),
                (b"Content-Length", b"1000"),
            ],
        )
    )
    c.send(h11.Data(data=b"x" * 1000))
    c.send(h11.EndOfMessage())


# Useful for manual benchmarking, e.g. with vmprof or on PyPy
def _run_basic_get_repeatedly():
    from timeit import default_timer

    REPEAT = 10000
    # while True:
    for _ in range(7):
        start = default_timer()
        for _ in range(REPEAT):
            time_server_basic_get_with_realistic_headers()
        finish = default_timer()
        print("{:.1f} requests/sec".format(REPEAT / (finish - start)))


if __name__ == "__main__":
    _run_basic_get_repeatedly()
