# used for methods, urls, and headers
def asciify(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    return s

def asciify_headers(headers):
    return [(asciify(f), asciify(v)) for (f, v) in headers]
