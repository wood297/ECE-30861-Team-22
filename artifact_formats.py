"""Non-mutating artifact format checks, independent of category classification.

Inputs are UTF-8 bytes (an optional UTF-8 BOM is accepted). Markdown is nonblank
text, excluding recognizable JSON/XML/HTML documents; Markdown may contain inline
HTML. HTML requires a recognized document/container tag, but is checked with the
tolerant stdlib HTML parser, not a browser or HTML conformance validator.

CycloneDX 1.0 through 1.6 are recognized. Checks establish the format marker,
version, and basic components shape, not full schema validity or SBOM completeness.
No schemas or other external resources are fetched. XML DTDs and entity
declarations are rejected, including internal declarations.
"""

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


SUPPORTED_CYCLONEDX_VERSIONS = frozenset(f"1.{minor}" for minor in range(7))
SUPPORTED_FORMATS = frozenset({"markdown", "html", "cyclonedx-json", "cyclonedx-xml"})


@dataclass(frozen=True)
class ValidationResult:
    original_bytes: bytes
    declared_format: str
    valid: bool
    code: str
    detail: str
    text: str | None = None
    cyclonedx_version: str | None = None


class _HTMLProbe(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.recognized = False

    def handle_starttag(self, tag, attrs):
        if tag in {"html", "head", "body", "article", "section", "div", "p", "h1", "table", "ul"}:
            self.recognized = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _parse_xml(text):
    # Forbid even unused/internal DTDs; no entity expansion or external I/O.
    return ElementTree.fromstring(text, forbid_dtd=True, forbid_entities=True, forbid_external=True)


def validate_artifact(content: bytes, declared_format: str) -> ValidationResult:
    """Validate an explicit canonical format; never infer category or rewrite bytes.

    ``code`` distinguishes unsupported format, encoding, empty content, and invalid
    content. ``text`` is a separate decoded view for future classification. Category
    matching remains the classifier's responsibility. Non-byte input is a caller error.
    """
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")

    text = None

    def result(valid, code, detail, version=None):
        return ValidationResult(content, declared_format, valid, code, detail, text, version)

    if declared_format not in SUPPORTED_FORMATS:
        return result(False, "unsupported_format", "Unsupported declared format")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result(False, "invalid_encoding", "Artifact must be UTF-8")
    if not text.strip():
        return result(False, "empty_content", "Artifact is empty or whitespace-only")
    if "\x00" in text:
        return result(False, "invalid_content", "Artifact contains a NUL character")

    version = None
    try:
        if declared_format == "cyclonedx-json":
            def reject_constant(value):
                raise ValueError("Nonstandard JSON constant")

            document = json.loads(text, parse_constant=reject_constant)
            if not isinstance(document, dict) or document.get("bomFormat") != "CycloneDX":
                raise ValueError("Expected a CycloneDX JSON object")
            version = document.get("specVersion")
            if not isinstance(version, str) or version not in SUPPORTED_CYCLONEDX_VERSIONS:
                raise ValueError("Unsupported CycloneDX version")
            if "components" in document and (
                not isinstance(document["components"], list)
                or any(not isinstance(item, dict) for item in document["components"])
            ):
                raise ValueError("CycloneDX components must be an array of objects")
        elif declared_format == "cyclonedx-xml":
            root = _parse_xml(text)
            match = re.fullmatch(r"\{http://cyclonedx\.org/schema/bom/(1\.\d+)\}bom", root.tag)
            if match is None or match[1] not in SUPPORTED_CYCLONEDX_VERSIONS:
                raise ValueError("Expected a supported CycloneDX XML bom namespace")
            version = match[1]
        elif declared_format == "html":
            if text.lstrip().startswith("<?xml"):
                raise ValueError("XML is not an HTML document")
            probe = _HTMLProbe()
            probe.feed(text)
            probe.close()
            if not probe.recognized:
                raise ValueError("Expected HTML document or container markup")
        else:
            stripped = text.lstrip()
            probe = _HTMLProbe()
            probe.feed(text)
            probe.close()
            if stripped.startswith("<") and probe.recognized:
                raise ValueError("HTML markup does not match declared Markdown format")
            if stripped.startswith(("{", "[", "<?xml", "<!DOCTYPE", "<!doctype")) or re.match(
                r"<(?:html|head|body|bom)(?:\s|>|/)", stripped, re.IGNORECASE
            ):
                raise ValueError("Structured document does not match declared Markdown format")
    except (ValueError, ElementTree.ParseError, DefusedXmlException):
        return result(False, "invalid_content", "Content does not match the declared format")
    return result(True, "valid", "Format validation passed", version)
