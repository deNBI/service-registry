"""
Logo Upload Utilities
=====================
Validates and sanitises uploaded logo files before they are stored on disk.

Entry point:  validate_and_process_logo(file_obj) -> InMemoryUploadedFile

Supported formats: PNG, JPEG, SVG
Security measures applied:
  - File size enforcement (configurable via site.toml LOGO_MAX_BYTES)
  - JPEG/PNG pixel-count limit checked from the header before decoding
    (configurable via site.toml LOGO_MAX_PIXELS)
  - SVG size limit checked before parsing (configurable via site.toml
    LOGO_MAX_SVG_BYTES)
  - Magic-byte type detection (extension/MIME header is never trusted)
  - JPEG/PNG: re-encoded via Pillow to strip EXIF metadata and verify integrity
  - SVG: parsed via stdlib xml.etree.ElementTree (safe on Python 3.12+ / Expat 2.7.1),
    root must be an SVG <svg>; only an allowlist of SVG drawing elements is kept;
    on* event attributes are dropped; a non-fragment href is dropped on <a> and
    rejects the file on any other element (e.g. a wrapped bitmap <image>); CSS
    references are limited to same-document fragments and data: URLs
  - UUID filenames are assigned by _logo_upload_to() in models.py; the original
    filename is discarded after this module returns.
"""

import io
import re

import xml.etree.ElementTree as _safe_et  # nosec B405 — Python 3.12 bundles Expat 2.7.1 (safe)
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile, UploadedFile
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
        "ping",
        "src",
    }
)

# The only namespaced attributes kept (xlink:href is still limited to #fragment
# links below). All others, including SVG-namespace-prefixed duplicates of plain
# attributes, xml:base and editor metadata (inkscape:, sodipodi:, ...), are dropped.
_KEPT_NAMESPACED_ATTRS: frozenset[str] = frozenset(
    {
        "{http://www.w3.org/1999/xlink}href",
        "{http://www.w3.org/XML/1998/namespace}space",
        "{http://www.w3.org/XML/1998/namespace}lang",
    }
)

# href / xlink:href variants — allowed only for same-document fragment refs
_HREF_ATTRS: frozenset[str] = frozenset({"href", "xlink:href"})

# Regex for event-handler attributes (onclick, onload, onmouseover, …)
_ON_ATTR_RE = re.compile(r"^on\w+$", re.IGNORECASE)

_SVG_NS = "http://www.w3.org/2000/svg"

# SVG elements kept in uploaded logos: shapes, text, paint servers, clipping,
# masking, filters and structure. Everything else (other namespaces, and SVG
# elements outside this list, together with their children) is removed.
_SVG_ALLOWED_ELEMENTS: frozenset[str] = frozenset(
    {
        "a", "circle", "clipPath", "defs", "desc", "ellipse", "feBlend",
        "feColorMatrix", "feComponentTransfer", "feComposite", "feConvolveMatrix",
        "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow",
        "feFlood", "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur",
        "feImage", "feMerge", "feMergeNode", "feMorphology", "feOffset",
        "fePointLight", "feSpecularLighting", "feSpotLight", "feTile",
        "feTurbulence", "filter", "g", "image", "line", "linearGradient", "marker",
        "mask", "metadata", "path", "pattern", "polygon", "polyline",
        "radialGradient", "rect", "stop", "style", "svg", "switch", "symbol",
        "text", "textPath", "title", "tspan", "use", "view",
    }
)  # fmt: skip

# CSS text can only reference a resource through one of these. Text without
# any of them, and without a backslash (which can hide one of them in an
# escape), is left as is.
_CSS_REFERENCE_HINT_RE = re.compile(
    r"url\(|image-set\(|image\(|src\(|@import", re.IGNORECASE
)
# CSS functions that reference images by plain string; not accepted.
_CSS_STRING_REF_FUNCTIONS: frozenset[str] = frozenset(
    {"image-set", "-webkit-image-set", "image", "src"}
)
_CSS_WHITESPACE = " \t\r\n\f"

# Maximum element nesting depth accepted in an uploaded SVG.
_SVG_MAX_DEPTH = 100

# Matches an SVG root element with an optional namespace prefix, e.g.
# "<svg ...>" or "<ns0:svg ...>" — used to recognise already-sanitised files.
_SVG_ROOT_RE = re.compile(r"^<[A-Za-z_][\w.-]*:svg[\s>/]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_size(file_obj) -> int:
    """Raise ValidationError if the upload exceeds LOGO_MAX_BYTES; return its size."""
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
        raise ValidationError(
            _(
                "Logo file is too large. Maximum allowed size is "
                f"{_format_size(max_bytes)}."
            )
        )
    return size


def _format_size(size: int) -> str:
    """1048576 -> "1 MB", 1500000 -> "1.4 MB", 2048 -> "2 KB" (one decimal)."""
    value, unit = (
        (size / (1024 * 1024), "MB") if size >= 1024 * 1024 else (size / 1024, "KB")
    )
    text = f"{value:.1f}"
    return f"{text[:-2] if text.endswith('.0') else text} {unit}"


def _check_svg_size(size: int) -> None:
    """Raise ValidationError if an SVG upload exceeds LOGO_MAX_SVG_BYTES.

    SVG processing (XML parsing, allowlist, CSS tokenizing) costs time in
    proportion to the file size, so SVGs have their own, smaller limit. It is
    checked before the file is parsed.
    """
    max_svg: int = getattr(settings, "LOGO_MAX_SVG_BYTES", 1024 * 1024)
    if size > max_svg:
        raise ValidationError(
            _(
                "SVG logo is too large. Maximum allowed size for SVG files is "
                f"{_format_size(max_svg)}."
            )
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
    # An SVG may start with an XML declaration, a comment (e.g. an editor's
    # "Generator" note) or a DOCTYPE before the <svg> root; the parser and the
    # root check in _sanitise_svg() decide whether it really is one.
    # Accept an optional namespace prefix on the root tag (e.g. <ns0:svg ...>),
    # which legacy records sanitised before the default-namespace fix carry.
    if (
        stripped.startswith(("<?xml", "<!--", "<svg"))
        or stripped[:9].lower() == "<!doctype"
        or _SVG_ROOT_RE.match(stripped)
    ):
        return "svg"

    raise ValidationError(
        _("Unsupported file type. Please upload a PNG, JPEG, or SVG file.")
    )


def _check_pixel_count(file_obj) -> None:
    """Raise ValidationError if the image exceeds LOGO_MAX_PIXELS.

    Only the header is read (Image.open is lazy), so oversized images are
    rejected before their pixel data is decoded.
    """
    from PIL import Image

    max_pixels: int = getattr(settings, "LOGO_MAX_PIXELS", 25_000_000)
    file_obj.seek(0)
    try:
        with Image.open(file_obj) as img:
            width, height = img.size
    except Image.DecompressionBombError as exc:
        # Pillow refuses headers this large before reporting their size.
        raise ValidationError(
            _(
                f"The image is larger than the {max_pixels / 1_000_000:g} "
                "megapixel maximum. Please upload a smaller version."
            )
        ) from exc
    except Exception:
        return  # unreadable: reported by the integrity check that follows
    finally:
        file_obj.seek(0)
    if width * height > max_pixels:
        raise ValidationError(
            _(
                f"The image is {width} × {height} pixels, larger than the "
                f"{max_pixels / 1_000_000:g} megapixel maximum. "
                "Please upload a smaller version."
            )
        )


def _strip_exif_jpeg(file_obj) -> bytes:
    """Re-encode a JPEG via Pillow, discarding all EXIF/metadata.

    Always outputs an RGB JPEG regardless of the source colour space:
    - CMYK/YCbCr/L are converted to RGB (some browsers cannot render non-RGB JPEGs).
    - RGBA/LA (alpha channel) is composited on a white background so transparent
      regions appear white rather than the black fill browsers would apply.
    - Palette images with a transparency hint are treated as RGBA.
    """
    from PIL import Image, UnidentifiedImageError

    _check_pixel_count(file_obj)

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
        img.load()  # Force full decode (triggers decompression-bomb check)
        # Composite alpha on white; normalise everything else to RGB.
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        elif img.mode == "P":
            if "transparency" in img.info:
                rgba = img.convert("RGBA")
                background = Image.new("RGB", img.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                img = background
            else:
                img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        raise ValidationError(_("Could not process the JPEG file.")) from exc


def _strip_exif_png(file_obj) -> bytes:
    """Re-encode a PNG via Pillow, discarding all metadata chunks."""
    from PIL import Image, UnidentifiedImageError

    _check_pixel_count(file_obj)

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


def _unsupported_reference() -> ValidationError:
    return ValidationError(
        _(
            "The SVG styles contain a resource reference in a form that is not "
            "supported. Please export the SVG with embedded resources only."
        )
    )


def _is_embedded_reference(target: str) -> bool:
    """True for same-document (#id) and embedded (data:) references."""
    target = target.strip(_CSS_WHITESPACE)
    return target.startswith("#") or target[:5].lower() == "data:"


def _clean_css(css: str, *, stylesheet: bool = False) -> str:
    """
    Limit CSS resource references to #fragment / data: and drop @import rules.

    The text is read with tinycss2 (a CSS Syntax-spec tokenizer). A url() to
    anything else is replaced with "none"; @import at the top of a stylesheet is
    removed; parse errors, @import elsewhere, string-based image functions and
    url() forms other than a single string are rejected. Text is re-serialised
    only when something was changed, so CSS that needs no change is kept
    byte for byte. The token walk is iterative (no recursion).
    """
    import tinycss2
    from tinycss2.ast import IdentToken

    # The plain-text check below cannot see escaped names ('u\72 l(' is
    # url(), '@\69mport' is @import), so text with a backslash is always
    # tokenized: tinycss2 decodes escapes in names and values.
    if "\\" not in css and not _CSS_REFERENCE_HINT_RE.search(css):
        return css

    if stylesheet:
        nodes = tinycss2.parse_stylesheet(css, skip_comments=False)
    else:
        nodes = tinycss2.parse_component_value_list(css, skip_comments=False)

    changed = False
    if stylesheet:
        kept = [
            n
            for n in nodes
            if not (n.type == "at-rule" and n.lower_at_keyword == "import")
        ]
        changed = len(kept) != len(nodes)
        nodes = kept

    pending = [nodes]
    while pending:
        group = pending.pop()
        for index, node in enumerate(group):
            kind = node.type
            if kind == "error":
                raise _unsupported_reference()
            if kind == "url":
                if not _is_embedded_reference(node.value):
                    group[index] = IdentToken(
                        node.source_line, node.source_column, "none"
                    )
                    changed = True
            elif kind == "function":
                if node.lower_name in _CSS_STRING_REF_FUNCTIONS:
                    raise _unsupported_reference()
                if node.lower_name == "url":
                    args = [
                        a
                        for a in node.arguments
                        if a.type not in ("whitespace", "comment")
                    ]
                    if len(args) != 1 or args[0].type != "string":
                        raise _unsupported_reference()
                    if not _is_embedded_reference(args[0].value):
                        group[index] = IdentToken(
                            node.source_line, node.source_column, "none"
                        )
                        changed = True
                else:
                    pending.append(node.arguments)
            elif kind in ("at-keyword", "at-rule"):
                keyword = getattr(node, "lower_at_keyword", None) or getattr(
                    node, "lower_value", ""
                )
                if keyword == "import":
                    raise _unsupported_reference()
                if kind == "at-rule":
                    pending.append(node.prelude)
                    if node.content is not None:
                        pending.append(node.content)
            elif kind == "qualified-rule":
                pending.append(node.prelude)
                pending.append(node.content)
            elif kind in ("() block", "[] block", "{} block"):
                pending.append(node.content)

    if not changed:
        return css
    try:
        return tinycss2.serialize(nodes)
    except RecursionError as exc:
        raise ValidationError(
            _("The SVG styles are nested too deeply to be processed.")
        ) from exc


def _check_depth(root) -> None:
    """Reject SVGs nested deeper than _SVG_MAX_DEPTH (walked without recursion)."""
    stack = [(root, 1)]
    while stack:
        elem, depth = stack.pop()
        if depth > _SVG_MAX_DEPTH:
            raise ValidationError(_("The SVG is nested too deeply to be processed."))
        stack.extend((child, depth + 1) for child in elem)


def _prune_elements(root) -> None:
    """Remove (with their subtree) elements that are not allowlisted SVG elements.

    The text that follows a removed element (its ElementTree "tail") belongs to
    the parent, e.g. the visible label after Visio metadata inside <text>, so it
    is moved to the previous kept sibling's tail or to the parent's text.

    Each parent's children are rebuilt once (linear time; removing children one
    by one is quadratic) and the moved text is joined once per slot.
    """
    parents = [root]
    while parents:
        parent = parents.pop()
        kept = []
        # Text pieces for the parent's text, then for each kept child's tail.
        slots: list[list[str]] = [[]]
        removed = False
        for child in parent:
            tag = child.tag if isinstance(child.tag, str) else ""
            ns, _sep, local = (
                tag[1:].partition("}") if tag.startswith("{") else ("", "", tag)
            )
            if ns == _SVG_NS and local in _SVG_ALLOWED_ELEMENTS:
                parents.append(child)
                kept.append(child)
                slots.append([])
            else:
                removed = True
                if child.tail:
                    slots[-1].append(child.tail)
        if not removed:
            continue
        if slots[0]:
            parent.text = (parent.text or "") + "".join(slots[0])
        for child, moved in zip(kept, slots[1:]):
            if moved:
                child.tail = (child.tail or "") + "".join(moved)
        parent[:] = kept


def _reject_outside_reference(elem) -> None:
    """Reject an element whose content comes from outside the file.

    Such references are removed, which would leave the element empty: an
    <image> wrapping a PNG or JPEG (a common way to export a bitmap logo as
    "SVG") would be stored as a blank logo. Only a link (<a>) keeps working
    without its href, so it is the one element whose reference is dropped
    silently.
    """
    local = elem.tag.rpartition("}")[2]
    if local == "a":
        return
    if local in ("image", "feImage"):
        raise ValidationError(
            _(
                "The SVG contains a bitmap image (<%(tag)s>), which is not "
                "supported in SVG logos. Please upload the bitmap as a PNG or "
                "JPEG file instead, or use a vector-only SVG."
            )
            % {"tag": local}
        )
    raise ValidationError(
        _(
            "The SVG uses content from outside the file (<%(tag)s>), which is "
            "not supported. Please export the SVG with all content inside the "
            "file."
        )
        % {"tag": local}
    )


def _sanitise_svg(file_obj) -> bytes:
    """
    Parse SVG with stdlib xml.etree.ElementTree (safe on Python 3.12+/Expat 2.7.1), then:
      - require an <svg> root in the SVG namespace
      - keep only allowlisted SVG elements (see _SVG_ALLOWED_ELEMENTS)
      - drop namespaced attributes other than xlink:href, xml:space, xml:lang
      - drop on* event-handler attributes and src/action/formaction/ping
      - drop href / xlink:href pointing outside the document on <a>, and
        reject the file when any other element has one (_reject_outside_reference)
      - limit CSS (style elements, style and presentation attributes) to
        #fragment / data: references
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
    if root.tag != f"{{{_SVG_NS}}}svg":
        raise ValidationError(
            _(
                "The file is not an SVG image: the root element must be <svg> "
                'with xmlns="http://www.w3.org/2000/svg".'
            )
        )

    # ElementTree does not preserve the default (unprefixed) namespace on
    # serialisation: a clean <svg xmlns="..."> would otherwise round-trip to
    # <ns0:svg xmlns:ns0="...">, which _sniff_type() no longer recognises as
    # SVG. Registering the SVG namespace with an empty prefix keeps the root
    # element as <svg>, making sanitisation idempotent across re-saves.
    _safe_et.register_namespace("", _SVG_NS)

    _check_depth(root)
    _prune_elements(root)

    # A <style> element's CSS is its whole text content; fold any child text
    # into one string so every part of it goes through _clean_css().
    for style in root.iter(f"{{{_SVG_NS}}}style"):
        css = "".join(style.itertext())
        del style[:]
        style.text = _clean_css(css, stylesheet=True)

    # Scrub dangerous attributes from all remaining elements
    for elem in root.iter():
        attrs_to_delete = []
        for attr, value in elem.attrib.items():
            if attr.startswith("{") and attr not in _KEPT_NAMESPACED_ATTRS:
                attrs_to_delete.append(attr)
                continue

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
                    _reject_outside_reference(elem)
                    attrs_to_delete.append(attr)

        for attr in attrs_to_delete:
            del elem.attrib[attr]

        # Un-namespaced attributes (style and presentation attributes such as
        # fill, filter, clip-path) are parsed as CSS by browsers.
        for attr, value in elem.attrib.items():
            if not attr.startswith("{"):
                elem.attrib[attr] = _clean_css(value)

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
      1. Enforce the file size limit (LOGO_MAX_BYTES).
      2. Detect type from magic bytes (never trust extension or MIME header).
      3. JPEG/PNG: check the pixel count from the header (LOGO_MAX_PIXELS), then
         re-encode via Pillow (strips EXIF, verifies integrity).
         SVG: check the SVG size limit (LOGO_MAX_SVG_BYTES), then parse and
         reduce it to the allowed elements, attributes and CSS references.
      4. Return an InMemoryUploadedFile containing the processed bytes.

    Raises django.core.exceptions.ValidationError on any failure.
    The returned file object should be assigned back to the form/serializer field;
    _logo_upload_to() in models.py will generate the final UUID filename.
    """
    size = _check_size(file_obj)
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
        _check_svg_size(size)
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


def logo_limits_text() -> str:
    """The configured upload limits as one sentence fragment, e.g.
    "PNG or JPEG up to 10 MB and 25 megapixels, or SVG up to 1 MB"."""
    max_bytes: int = getattr(settings, "LOGO_MAX_BYTES", 10 * 1024 * 1024)
    max_pixels: int = getattr(settings, "LOGO_MAX_PIXELS", 25_000_000)
    max_svg: int = min(getattr(settings, "LOGO_MAX_SVG_BYTES", 1024 * 1024), max_bytes)
    return (
        f"PNG or JPEG up to {_format_size(max_bytes)} and "
        f"{max_pixels / 1_000_000:g} megapixels, or SVG up to {_format_size(max_svg)}"
    )


def logo_help_text() -> str:
    """Help text for logo fields (admin, API, form fallback)."""
    return f"Optional. {logo_limits_text()}."


def process_new_logo_upload(value):
    """
    Form-field helper: validate and process a freshly uploaded logo.

    Anything else (None, False from the "clear" checkbox, or the already-stored
    FieldFile that Django hands back when no new file was chosen) is returned
    unchanged, so saving a record never rewrites or re-checks its stored logo.
    """
    if isinstance(value, UploadedFile):
        return validate_and_process_logo(value)
    return value
