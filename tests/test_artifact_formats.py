"""Standalone format validation; no external services or fixture changes."""

import pytest

from artifact_formats import validate_artifact


@pytest.mark.parametrize("format_name,content", [
    ("markdown", "# Instructions\r\n\r\nInstall café — 日本語.  \r\n"),
    ("html", "<!doctype html><html><body><p>café 日本語</p></body></html>"),
    ("html", "<p>Instructions<br>Install now"),
    ("cyclonedx-json", '{"bomFormat":"CycloneDX","specVersion":"1.5","components":[]}'),
    ("cyclonedx-xml", '<bom xmlns="http://cyclonedx.org/schema/bom/1.6"><components/></bom>'),
])
def test_valid_formats_preserve_original_bytes(format_name, content):
    original = content.encode("utf-8")
    result = validate_artifact(original, format_name)
    assert result.valid
    assert result.code == "valid"
    assert result.original_bytes is original
    assert result.text == content


@pytest.mark.parametrize("format_name,content", [
    ("pdf", b"%PDF-1.7"),
    ("markdown", b""),
    ("html", b" \r\n\t"),
    ("markdown", b"\xff"),
    ("markdown", b"abc\x00def"),
    ("html", b"plain text"),
    ("html", b'{"bomFormat":"CycloneDX"}'),
    ("markdown", b"<html><body>Instructions</body></html>"),
    ("markdown", b"<p>Instructions</p>"),
    ("markdown", b'{"bomFormat":"CycloneDX"}'),
    ("cyclonedx-json", b"# SBOM"),
    ("cyclonedx-json", b'{"bomFormat":'),
    ("cyclonedx-json", b"[]"),
    ("cyclonedx-json", b'{"bomFormat":"Other","specVersion":"1.5"}'),
    ("cyclonedx-json", b'{"bomFormat":"CycloneDX","specVersion":"9.0"}'),
    ("cyclonedx-json", b'{"bomFormat":"CycloneDX","specVersion":"1.5","components":{}}'),
    ("cyclonedx-json", b'{"bomFormat":"CycloneDX","specVersion":"1.5","x":NaN}'),
    ("cyclonedx-xml", b"<bom>"),
    ("cyclonedx-xml", b"<bom/>"),
    ("cyclonedx-xml", b'<bom xmlns="http://cyclonedx.org/schema/bom/9.0"/>'),
    ("cyclonedx-xml", b'<other xmlns="http://cyclonedx.org/schema/bom/1.5"/>'),
    ("cyclonedx-xml", b'{"bomFormat":"CycloneDX","specVersion":"1.5"}'),
])
def test_invalid_inputs(format_name, content):
    result = validate_artifact(content, format_name)
    assert not result.valid
    assert result.original_bytes is content
    assert result.code != "valid"


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE bom SYSTEM "https://example.invalid/schema.dtd">',
    '<!DOCTYPE bom [<!ENTITY secret SYSTEM "file:///etc/passwd">]>',
    '<!DOCTYPE bom [<!ENTITY word "expanded">]>',
])
def test_xml_rejects_dtds_and_entities(declaration):
    content = (declaration + '<bom xmlns="http://cyclonedx.org/schema/bom/1.5"/>').encode()
    assert not validate_artifact(content, "cyclonedx-xml").valid


@pytest.mark.parametrize("content", [
    '<!DOCTYPE bom><bom xmlns="http://cyclonedx.org/schema/bom/1.5"/>',
    '<!DOCTYPE bom [<!ENTITY x SYSTEM "https://example.invalid/secret">]>'
    '<bom xmlns="http://cyclonedx.org/schema/bom/1.5">&x;</bom>',
    '<!DOCTYPE bom [<!ENTITY % x SYSTEM "https://example.invalid/dtd">%x;]>'
    '<bom xmlns="http://cyclonedx.org/schema/bom/1.5"/>',
    '<!DOCTYPE bom [<!ENTITY a "expanded"><!ENTITY b "&a;&a;&a;">]>'
    '<bom xmlns="http://cyclonedx.org/schema/bom/1.5">&b;</bom>',
    '<bom xmlns="http://cyclonedx.org/schema/bom/1.5">&undeclared;</bom>',
])
def test_malicious_xml_is_rejected_without_external_access(content, monkeypatch):
    import socket
    import urllib.request

    def forbidden_access(*args, **kwargs):
        pytest.fail("XML validation attempted external access")

    monkeypatch.setattr(socket, "create_connection", forbidden_access)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_access)
    original = content.encode()
    result = validate_artifact(original, "cyclonedx-xml")
    assert not result.valid
    assert result.code == "invalid_content"
    assert result.original_bytes is original


def test_xml_predefined_entities_remain_valid():
    original = b'<bom xmlns="http://cyclonedx.org/schema/bom/1.5"><!-- safe -->&amp;&lt;</bom>\r\n'
    result = validate_artifact(original, "cyclonedx-xml")
    assert result.valid
    assert result.original_bytes is original


@pytest.mark.parametrize("format_name,body", [
    ("cyclonedx-json", '{\r\n"bomFormat":"CycloneDX","specVersion":"1.6","metadata":{"x":"é"}\r\n}'),
    ("cyclonedx-xml", '<bom xmlns="http://cyclonedx.org/schema/bom/1.6">\r\n<!-- é -->\r\n</bom>'),
])
def test_structured_unicode_crlf_and_version(format_name, body):
    original = body.encode()
    result = validate_artifact(original, format_name)
    assert result.valid
    assert result.cyclonedx_version == "1.6"
    assert result.original_bytes == original
    assert result.text == body


def test_bom_is_preserved_in_original():
    original = b"\xef\xbb\xbf# Instructions\r\n"
    result = validate_artifact(original, "markdown")
    assert result.valid
    assert result.original_bytes is original
    assert result.text == "# Instructions\r\n"


def test_non_bytes_are_caller_errors():
    with pytest.raises(TypeError):
        validate_artifact("text", "markdown")
