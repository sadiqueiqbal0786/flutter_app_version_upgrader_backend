import time

from app.signing import sign, verify


def test_sign_and_verify_roundtrip():
    ts, sig = sign("secret", b'{"a":1}')
    assert sig.startswith("sha256=")
    assert verify("secret", b'{"a":1}', ts, sig)
    assert not verify("secret", b'{"a":2}', ts, sig)
    assert not verify("other", b'{"a":1}', ts, sig)


def test_stale_timestamp_rejected():
    old = int(time.time()) - 1000
    ts, sig = sign("secret", b"x", old)
    assert not verify("secret", b"x", ts, sig)
    assert not verify("secret", b"x", "not-a-number", sig)
