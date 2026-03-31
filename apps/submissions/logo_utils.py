"""
Logo Upload Utilities
=====================
Validates and sanitises uploaded logo files before they are stored on disk.

Entry point:  validate_and_process_logo(file_obj) -> InMemoryUploadedFile

Supported formats: PNG, JPEG, SVG
Security measures applied:
  - File size enforcement (configurable via site.toml LOGO_MAX_BYTES)
  - Magic-byte type detection (extension/MIME header is never trusted)
  - JPEG/PNG: re-encoded via Pillow to strip EXIF metadata and verify integrity
  - SVG: parsed via stdlib xml.etree.ElementTree (safe on Python 3.12+ / Expat 2.7.1)
    then scrubbed of <script> elements, on* event attributes, and non-fragment external hrefs
  - UUID filenames are assigned by _logo_upload_to() in models.py; the original
    filename is discarded after this module returns.

Note on SVG security: This implementation covers the most common attack vectors
(injected scripts, event handlers, external requests). CSS-based side-channels
(e.g. url() in <style> tags) and exotic browser-quirk vectors are not fully
mitigated. If stricter guarantees are needed, consider rejecting SVG entirely or
rendering to raster via cairosvg before storage.
"""

import io
import re

import xml.etree.ElementTree as _safe_et  # nosec B405 — Python 3.12 bundles Expat 2.7.1 (safe)
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils.translation import gettext_lazy as _
from xml.etree.ElementTree import tostring as _et_tostring

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG"

# SVG attributes that must never appear regardless of value
_FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "action",
        "formaction",
        "src",
    }
)

# href / xlink:href variants — allowed only for same-document fragment refs
_HREF_ATTRS: frozenset[str] = frozenset({"href", "xlink:href"})

# Regex for event-handler attributes (onclick, onload, onmouseover, …)
_ON_ATTR_RE = re.compile(r"^on\w+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_size(file_obj) -> None:
    """Raise ValidationError if the upload exceeds LOGO_MAX_BYTES."""
    max_bytes: int = getattr(settings, "LOGO_MAX_BYTES", 10 * 1024 * 1024)
    # file_obj.size is set by Django's upload handler
    size = getattr(file_obj, "size", None)
    if size is None:
        # Fall back to seeking for streams without a .size attribute
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(0)
    if size == 0:
        raise ValidationError(_("The uploaded file is empty."))
    if size > max_bytes:
        mb = max_bytes / (1024 * 1024)
        raise ValidationError(
            _(f"Logo file is too large. Maximum allowed size is {mb:.0f} MB.")
        )


def _sniff_type(file_obj) -> str:
    """
    Detect file type from magic bytes.
    Returns 'png', 'jpeg', or 'svg'.
    Raises ValidationError for anything else.
    """
    file_obj.seek(0)
    header = file_obj.read(8)
    file_obj.seek(0)

    if header[:3] == _JPEG_MAGIC:
        return "jpeg"
    if header[:4] == _PNG_MAGIC:
        return "png"

    # SVG detection: read more bytes and look for XML/SVG indicators
    file_obj.seek(0)
    # Strip BOM if present
    sample = file_obj.read(512)
    file_obj.seek(0)
    if isinstance(sample, bytes):
        sample_str = sample.lstrip(b"\xef\xbb\xbf").decode("utf-8", errors="replace")
    else:
        sample_str = sample
    stripped = sample_str.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<svg"):
        return "svg"

    raise ValidationError(
        _("Unsupported file type. Please upload a PNG, JPEG, or SVG file.")
    )


def _strip_exif_jpeg(file_obj) -> bytes:
    """Re-encode a JPEG via Pillow, discarding all EXIF/metadata."""
    from PIL import Image, UnidentifiedImageError

    try:
        file_obj.seek(0)
        img = Image.open(file_obj)
        img.verify()  # Detect corrupt headers — closes the image after verify
    except (UnidentifiedImageError, Exception) as exc:
        raise ValidationError(
            _("The uploaded JPEG file is corrupt or unreadable.")
        ) from exc

    try:
        file_obj.seek(0)
        img = Image.open(file_obj)
        img = img.convert(
            img.mode
        )  # Force full decode (triggers decompression-bomb check)
        out = io.BytesIO()
        img.save(out, format="JPEG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        raise ValidationError(_("Could not process the JPEG file.")) from exc


def _strip_exif_png(file_obj) -> bytes:
    """Re-encode a PNG via Pillow, discarding all metadata chunks."""
    from PIL import Image, UnidentifiedImageError

    try:
        file_obj.seek(0)
        img = Image.open(file_obj)
        img.verify()
    except (UnidentifiedImageError, Exception) as exc:
        raise ValidationError(
            _("The uploaded PNG file is corrupt or unreadable.")
        ) from exc

    try:
        file_obj.seek(0)
        img = Image.open(file_obj)
        img = img.convert(img.mode)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        raise ValidationError(_("Could not process the PNG file.")) from exc


def _sanitise_svg(file_obj) -> bytes:
    """
    Parse SVG with stdlib xml.etree.ElementTree (safe on Python 3.12+/Expat 2.7.1) then scrub:
      - All <script> elements (any namespace)
      - All on* event-handler attributes
      - href / xlink:href pointing outside the document (non-fragment URLs)
      - src, action, formaction attributes (potential external-resource leaks)
    Returns sanitised SVG as UTF-8 bytes.
    """
    try:
        file_obj.seek(0)
        tree = _safe_et.parse(file_obj)
    except Exception as exc:
        raise ValidationError(
            _("The SVG file could not be parsed. Ensure it is valid XML.")
        ) from exc

    root = tree.getroot()

    # Build parent map so we can remove child elements safely
    parent_map: dict = {}
    for parent in root.iter():
        for child in parent:
            parent_map[child] = parent

    # Collect elements to remove (cannot mutate while iterating)
    to_remove: list = []
    for elem in root.iter():
        tag = elem.tag
        # Strip namespace prefix for tag name comparison
        local = tag.split("}")[-1].lower() if "}" in tag else tag.lower()
        if local == "script":
            to_remove.append(elem)

    for elem in to_remove:
        parent = parent_map.get(elem)
        if parent is not None:
            parent.remove(elem)

    # Scrub dangerous attributes from all remaining elements
    for elem in root.iter():
        attrs_to_delete = []
        for attr, value in elem.attrib.items():
            local_attr = attr.split("}")[-1] if "}" in attr else attr
            local_lower = local_attr.lower()

            if _ON_ATTR_RE.match(local_lower):
                attrs_to_delete.append(attr)
                continue

            if local_lower in _FORBIDDEN_ATTRS:
                attrs_to_delete.append(attr)
                continue

            if local_lower in ("href", "xlink:href") or attr in _HREF_ATTRS:
                # Allow same-document fragment references (#id), block everything else
                if value and not value.strip().startswith("#"):
                    attrs_to_delete.append(attr)

        for attr in attrs_to_delete:
            del elem.attrib[attr]

    try:
        svg_str = _et_tostring(root, encoding="unicode")
        return svg_str.encode("utf-8")
    except Exception as exc:
        raise ValidationError(_("Failed to serialise the sanitised SVG.")) from exc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_and_process_logo(file_obj) -> InMemoryUploadedFile:
    """
    Validate and sanitise an uploaded logo file.

    Steps:
      1. Enforce file size limit.
      2. Detect type from magic bytes (never trust extension or MIME header).
      3. Re-encode JPEG/PNG via Pillow (strips EXIF, verifies integrity).
         Parse and scrub SVG via stdlib xml.etree.ElementTree (blocks XML attacks + event handlers).
      4. Return an InMemoryUploadedFile containing the processed bytes.

    Raises django.core.exceptions.ValidationError on any failure.
    The returned file object should be assigned back to the form/serializer field;
    _logo_upload_to() in models.py will generate the final UUID filename.
    """
    _check_size(file_obj)
    mime_type = _sniff_type(file_obj)
    file_obj.seek(0)

    if mime_type == "jpeg":
        data = _strip_exif_jpeg(file_obj)
        content_type = "image/jpeg"
        ext = "jpg"
    elif mime_type == "png":
        data = _strip_exif_png(file_obj)
        content_type = "image/png"
        ext = "png"
    else:  # svg
        data = _sanitise_svg(file_obj)
        content_type = "image/svg+xml"
        ext = "svg"

    buf = io.BytesIO(data)
    return InMemoryUploadedFile(
        file=buf,
        field_name="logo",
        name=f"logo.{ext}",  # Placeholder — _logo_upload_to() replaces this with a UUID path
        content_type=content_type,
        size=len(data),
        charset=None,
    )
