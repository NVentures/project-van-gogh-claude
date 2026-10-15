"""Regression guard for the stdout-encoding crash that killed auto-ingest.

On Windows, stdout defaults to the locale codec (cp1252). Scripts that print
box-drawing or status glyphs (── ✓ ✗ ·) then raise UnicodeEncodeError on the
first print() and crash. This is exactly how the hourly Granola auto-ingest
silently filed zero meetings for a week, despite a 30-meeting backlog being
ready: ``meeting_ingest.py --auto`` died on its first ``print(f"── {title}")``.

``config_loader.force_utf8_io()`` is the fix — entry-point ``main()`` functions
call it to make stdout/stderr UTF-8 regardless of console codepage. This is the
stdout sibling of ``test_open_encoding``'s file-I/O guard.
"""
import io
import sys

from config_loader import force_utf8_io

# The glyphs that appear in app/ print() statements and crash under cp1252.
_UNICODE_GLYPHS = "── ✓ ✗ ·"  # ── ✓ ✗ ·


def test_force_utf8_io_allows_unicode_print_under_cp1252():
    out_buf, err_buf = io.BytesIO(), io.BytesIO()
    # Keep references so the wrappers are not GC'd (which would close the buffers).
    out_wrap = io.TextIOWrapper(out_buf, encoding="cp1252")
    err_wrap = io.TextIOWrapper(err_buf, encoding="cp1252")
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_wrap, err_wrap
    try:
        force_utf8_io()
        # Without the fix this raises UnicodeEncodeError and aborts the script.
        print(_UNICODE_GLYPHS)
        out_wrap.flush()
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err

    assert _UNICODE_GLYPHS.encode("utf-8") in out_buf.getvalue()


def test_force_utf8_io_is_safe_on_streams_without_reconfigure():
    # Streams lacking .reconfigure (e.g. a plain object) must be left alone,
    # not raise — force_utf8_io is best-effort.
    orig_out = sys.stdout
    sys.stdout = object()  # no .reconfigure attribute
    try:
        force_utf8_io()  # must not raise
    finally:
        sys.stdout = orig_out
