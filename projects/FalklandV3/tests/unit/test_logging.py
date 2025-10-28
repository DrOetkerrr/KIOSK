from io import StringIO

from falklandv3.utils.logging import log


def test_structured_log_output():
    stream = StringIO()
    log("test.event", stream=stream, key="value")
    out = stream.getvalue().strip()
    assert '"event": "test.event"' in out
    assert '"key": "value"' in out
    assert '"ts":' in out
