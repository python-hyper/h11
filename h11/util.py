__all__ = ["bytesify"]

# used for methods, urls, header names, and header values
def bytesify(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    return bytes(s)
