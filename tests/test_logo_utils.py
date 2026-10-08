"""
Tests for apps/submissions/logo_utils.py

Covers:
  - Magic byte type detection
  - File size enforcement
  - JPEG/PNG EXIF stripping via Pillow
  - SVG sanitisation (script removal, event handlers, href scrubbing)
  - XML attack prevention (XXE, billion-laughs) — stdlib ET safe on Python 3.12+/Expat 2.7.1
  - Output type and filename
  - Path traversal prevention
"""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile, SimpleUploadedFile

from apps.submissions.logo_utils import validate_and_process_logo


# ---------------------------------------------------------------------------
# Minimal valid test files
# ---------------------------------------------------------------------------


def _make_jpeg_bytes() -> bytes:
    """Return a minimal valid 1×1 white JPEG."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes() -> bytes:
    """Return a minimal valid 1×1 white PNG."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_svg_bytes(content: str = "") -> bytes:
    """Return a minimal valid SVG."""
    svg = (
        content
        or '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    )
    return svg.encode("utf-8")


def _make_upload(data: bytes, name: str = "test.png", content_type: str = "image/png"):
    return SimpleUploadedFile(name, data, content_type=content_type)


# ---------------------------------------------------------------------------
# Magic byte / type detection
# ---------------------------------------------------------------------------


class TestMagicByteValidation:
    def test_valid_jpeg_accepted(self):
        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        assert result is not None

    def test_valid_png_accepted(self):
        f = _make_upload(_make_png_bytes(), "logo.png", "image/png")
        result = validate_and_process_logo(f)
        assert result is not None

    def test_valid_svg_with_xml_decl_accepted(self):
        data = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert result is not None

    def test_valid_svg_without_xml_decl_accepted(self):
        data = _make_svg_bytes()
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert result is not None

    def test_random_bytes_rejected(self):
        f = _make_upload(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08", "file.bin")
        with pytest.raises(ValidationError, match="Unsupported file type"):
            validate_and_process_logo(f)

    def test_text_file_rejected_even_if_named_png(self):
        f = _make_upload(b"Hello, this is plain text.", "logo.png", "image/png")
        with pytest.raises(ValidationError, match="Unsupported file type"):
            validate_and_process_logo(f)

    def test_wrong_extension_does_not_bypass_validation(self):
        # PNG magic bytes in a file named .svg — still detected as PNG, processed correctly
        f = _make_upload(_make_png_bytes(), "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert result.name.endswith(".png")

    def test_empty_file_rejected(self):
        f = _make_upload(b"", "logo.png", "image/png")
        with pytest.raises(ValidationError, match="empty"):
            validate_and_process_logo(f)


# ---------------------------------------------------------------------------
# File size limits
# ---------------------------------------------------------------------------


class TestSizeLimits:
    def test_file_within_limit_accepted(self, settings):
        settings.LOGO_MAX_BYTES = 1024 * 1024  # 1 MB
        data = _make_png_bytes()
        assert len(data) < settings.LOGO_MAX_BYTES
        f = _make_upload(data, "logo.png")
        result = validate_and_process_logo(f)
        assert result is not None

    def test_file_over_limit_rejected(self, settings):
        settings.LOGO_MAX_BYTES = 10  # 10 bytes — guaranteed to be smaller than any PNG
        f = _make_upload(_make_png_bytes(), "logo.png")
        with pytest.raises(ValidationError, match="too large"):
            validate_and_process_logo(f)


def _svg_of_size(size: int) -> bytes:
    """A valid SVG of exactly ``size`` bytes (padded with a comment)."""
    head = b'<svg xmlns="http://www.w3.org/2000/svg"><!--'
    tail = b'--><rect width="1" height="1"/></svg>'
    return head + b"x" * (size - len(head) - len(tail)) + tail


class TestSvgSizeLimit:
    def test_svg_at_limit_accepted(self, settings):
        settings.LOGO_MAX_SVG_BYTES = 2048
        f = _make_upload(_svg_of_size(2048), "logo.svg", "image/svg+xml")
        assert validate_and_process_logo(f) is not None

    def test_svg_over_limit_rejected(self, settings):
        settings.LOGO_MAX_SVG_BYTES = 2048
        f = _make_upload(_svg_of_size(2049), "logo.svg", "image/svg+xml")
        with pytest.raises(ValidationError, match="SVG logo is too large.*2 KB"):
            validate_and_process_logo(f)

    def test_limit_applies_to_svg_only(self, settings):
        settings.LOGO_MAX_SVG_BYTES = 10
        f = _make_upload(_make_png_bytes(), "logo.png")
        assert len(_make_png_bytes()) > 10
        assert validate_and_process_logo(f) is not None

    def test_over_limit_svg_is_rejected_before_parsing(self, settings, monkeypatch):
        import apps.submissions.logo_utils as logo_utils

        parses = []
        real_parse = logo_utils._safe_et.parse
        monkeypatch.setattr(
            logo_utils._safe_et,
            "parse",
            lambda *a, **k: parses.append(1) or real_parse(*a, **k),
        )
        settings.LOGO_MAX_SVG_BYTES = 2048
        f = _make_upload(_svg_of_size(4096), "logo.svg", "image/svg+xml")
        with pytest.raises(ValidationError):
            validate_and_process_logo(f)
        assert parses == []

    def test_default_svg_limit_is_one_megabyte(self):
        from django.conf import settings as django_settings

        assert django_settings.LOGO_MAX_SVG_BYTES == 1024 * 1024


def _make_raster_bytes(fmt: str, size: tuple[int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format=fmt)
    return buf.getvalue()


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG with only IHDR + IEND: claims a size without any pixel data."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


class TestLimitMessages:
    def test_size_message_uses_kilobytes_below_one_megabyte(self, settings):
        settings.LOGO_MAX_BYTES = 512 * 1024
        f = _make_upload(b"\x89PNG" + b"0" * (600 * 1024), "logo.png")
        with pytest.raises(ValidationError, match="512 KB"):
            validate_and_process_logo(f)

    def test_sizes_are_rounded_to_one_decimal(self):
        from apps.submissions.logo_utils import _format_size

        assert _format_size(1_500_000) == "1.4 MB"
        assert _format_size(1024 * 1024) == "1 MB"
        assert _format_size(10 * 1024 * 1024) == "10 MB"
        assert _format_size(2048) == "2 KB"
        assert _format_size(1500) == "1.5 KB"

    def test_huge_image_header_gets_the_size_message(self):
        # Pillow refuses to open headers this large, so the message has no
        # dimensions, but it is the size message rather than "corrupt".
        f = _make_upload(_png_header_only(20_000, 20_000), "logo.png")
        with pytest.raises(ValidationError, match="larger than the 25 megapixel"):
            validate_and_process_logo(f)

    def test_corrupt_png_still_gets_the_corrupt_message(self):
        f = _make_upload(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, "logo.png")
        with pytest.raises(ValidationError, match="corrupt or unreadable"):
            validate_and_process_logo(f)


class TestPixelLimits:
    @pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
    def test_image_at_pixel_limit_accepted(self, settings, fmt):
        settings.LOGO_MAX_PIXELS = 100
        f = _make_upload(_make_raster_bytes(fmt, (10, 10)), f"logo.{fmt.lower()}")
        assert validate_and_process_logo(f) is not None

    @pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
    def test_image_over_pixel_limit_rejected(self, settings, fmt):
        settings.LOGO_MAX_PIXELS = 100
        f = _make_upload(_make_raster_bytes(fmt, (11, 10)), f"logo.{fmt.lower()}")
        with pytest.raises(ValidationError, match="11 × 10"):
            validate_and_process_logo(f)

    def test_over_limit_image_is_rejected_before_decoding(self, settings, monkeypatch):
        from PIL import ImageFile

        settings.LOGO_MAX_PIXELS = 100
        f = _make_upload(_make_raster_bytes("PNG", (11, 10)), "logo.png")
        # ImageFile.load is where Pillow decodes pixel data from a file.
        loads = []
        real_load = ImageFile.ImageFile.load
        monkeypatch.setattr(
            ImageFile.ImageFile,
            "load",
            lambda self: loads.append(1) or real_load(self),
        )
        with pytest.raises(ValidationError):
            validate_and_process_logo(f)
        assert loads == []


# ---------------------------------------------------------------------------
# JPEG processing
# ---------------------------------------------------------------------------


def _make_jpeg_bytes_from_mode(mode: str, size=(4, 4), fill=None) -> bytes:
    """Create a minimal JPEG from an image in the given Pillow mode."""
    from PIL import Image

    if fill is None:
        fill = (128,) * len(mode) if mode != "P" else 128
    if mode == "P":
        img = Image.new("RGB", size, (128, 128, 128)).convert("P")
    else:
        img = Image.new(mode, size, fill)
    buf = io.BytesIO()
    if mode in ("RGBA", "LA"):
        # Pillow cannot save RGBA/LA as JPEG; save as PNG then re-read to
        # produce a file that has JPEG magic bytes via patching.
        # Instead, we produce the bytes via the PNG path and then feed it
        # through as PNG in the JPEG test helper is not applicable here;
        # what matters is the _strip_exif_jpeg function handles the mode
        # correctly when it encounters it internally (e.g. from JPEG extensions).
        # We test via the internal helper directly.
        img.save(buf, format="PNG")
    else:
        try:
            img.save(buf, format="JPEG")
        except OSError:
            img = img.convert("RGB")
            img.save(buf, format="JPEG")
    return buf.getvalue()


class TestJpegProcessing:
    def test_output_is_valid_jpeg(self):
        from PIL import Image

        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        img = Image.open(result.file)
        assert img.format == "JPEG"

    def test_output_is_inmemoryuploadedfile(self):
        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        assert isinstance(result, InMemoryUploadedFile)

    def test_output_content_type_is_jpeg(self):
        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        assert result.content_type == "image/jpeg"

    def test_output_name_ends_with_jpg(self):
        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        assert result.name.endswith(".jpg")

    def test_corrupt_jpeg_rejected(self):
        # JPEG magic bytes but garbage body
        data = b"\xff\xd8\xff" + b"\x00" * 50
        f = _make_upload(data, "logo.jpg", "image/jpeg")
        with pytest.raises(ValidationError):
            validate_and_process_logo(f)

    def test_output_is_always_rgb(self):
        """JPEG output must always be RGB regardless of input colour space."""
        from PIL import Image

        f = _make_upload(_make_jpeg_bytes(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        img = Image.open(result.file)
        assert img.mode == "RGB"

    def test_grayscale_jpeg_converted_to_rgb(self):
        """L-mode (grayscale) JPEG is converted to RGB on output."""
        from PIL import Image

        buf = io.BytesIO()
        Image.new("L", (4, 4), 200).save(buf, format="JPEG")
        f = _make_upload(buf.getvalue(), "logo.jpg", "image/jpeg")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        img = Image.open(result.file)
        assert img.mode == "RGB"

    def test_rgba_composited_on_white_not_black(self):
        """
        RGBA images (alpha channel) must be composited on white, not black.

        Pillow can open some edge-case JPEGs in RGBA mode; the re-encode
        path must produce white (not black) pixels for fully-transparent areas.
        We test _strip_exif_jpeg directly by patching PIL.Image.open to
        return a fully-transparent RGBA image on the second call (the
        processing pass that follows verify()).
        """
        import unittest.mock as mock
        from PIL import Image
        from apps.submissions.logo_utils import _strip_exif_jpeg

        call_count = {"n": 0}
        original_open = Image.open

        def _patched_open(fp, *args, **kwargs):
            call_count["n"] += 1
            img = original_open(fp, *args, **kwargs)
            if call_count["n"] == 2:
                # Second call is the processing pass — return RGBA with full transparency.
                rgba = Image.new("RGBA", img.size, (255, 0, 0, 0))
                return rgba
            return img

        jpeg_buf = io.BytesIO(_make_jpeg_bytes())
        with mock.patch("PIL.Image.open", side_effect=_patched_open):
            result_bytes = _strip_exif_jpeg(jpeg_buf)

        out = Image.open(io.BytesIO(result_bytes))
        assert out.mode == "RGB"
        # Verify compositing produced a light (near-white) result, not the black
        # fill that Pillow used to apply when saving RGBA as JPEG.
        # We use > 200 rather than == 255 because JPEG is lossy and may shift
        # exact values slightly.  get_flattened_data() is the non-deprecated
        # replacement for getdata() (Pillow 14+); it returns the same tuple sequence.
        assert all(all(c > 200 for c in pixel) for pixel in out.get_flattened_data()), (
            "Transparent pixels should be composited on white, not filled with black"
        )


# ---------------------------------------------------------------------------
# PNG processing
# ---------------------------------------------------------------------------


class TestPngProcessing:
    def test_output_is_valid_png(self):
        from PIL import Image

        f = _make_upload(_make_png_bytes(), "logo.png", "image/png")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        img = Image.open(result.file)
        assert img.format == "PNG"

    def test_output_is_inmemoryuploadedfile(self):
        f = _make_upload(_make_png_bytes(), "logo.png", "image/png")
        result = validate_and_process_logo(f)
        assert isinstance(result, InMemoryUploadedFile)

    def test_output_content_type_is_png(self):
        f = _make_upload(_make_png_bytes(), "logo.png", "image/png")
        result = validate_and_process_logo(f)
        assert result.content_type == "image/png"

    def test_output_name_ends_with_png(self):
        f = _make_upload(_make_png_bytes(), "logo.png", "image/png")
        result = validate_and_process_logo(f)
        assert result.name.endswith(".png")


# ---------------------------------------------------------------------------
# SVG sanitisation
# ---------------------------------------------------------------------------


class TestSvgSanitisation:
    def _process_svg(self, svg_content: str) -> str:
        data = svg_content.encode("utf-8")
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        return result.file.read().decode("utf-8")

    def test_script_tag_is_removed(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        output = self._process_svg(svg)
        assert "<script" not in output
        assert "alert" not in output

    def test_onclick_attribute_is_removed(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)"/></svg>'
        output = self._process_svg(svg)
        assert "onclick" not in output

    def test_onload_attribute_is_removed(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'//evil.com\')"></svg>'
        output = self._process_svg(svg)
        assert "onload" not in output

    def test_external_href_is_removed(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><a href="https://evil.com">click</a></svg>'
        output = self._process_svg(svg)
        assert "https://evil.com" not in output

    def test_fragment_href_is_preserved(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><use href="#myshape"/></svg>'
        output = self._process_svg(svg)
        assert 'href="#myshape"' in output

    def test_src_attribute_is_removed(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><image src="https://evil.com/t.gif"/></svg>'
        output = self._process_svg(svg)
        assert "https://evil.com" not in output

    def test_clean_svg_passes_through(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40"/></svg>'
        output = self._process_svg(svg)
        # ElementTree may serialise with namespace prefix (ns0:circle) — match element name
        assert "circle" in output

    def test_output_is_inmemoryuploadedfile(self):
        data = _make_svg_bytes()
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert isinstance(result, InMemoryUploadedFile)

    def test_output_content_type_is_svg(self):
        data = _make_svg_bytes()
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert result.content_type == "image/svg+xml"

    def test_output_name_ends_with_svg(self):
        data = _make_svg_bytes()
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        assert result.name.endswith(".svg")


# ---------------------------------------------------------------------------
# Re-validation / idempotency regression
#
# Scenario: upload an SVG logo (validated + sanitised + stored), then save the
# record again without changing the logo. The already-sanitised file is fed back
# through validate_and_process_logo(). Before the fix, ElementTree re-serialised
# the SVG root as "<ns0:svg ...>", which _sniff_type() no longer recognised as
# SVG, raising "Unsupported file type" on the second save. PNG/JPEG were immune
# because Pillow re-encodes to bytes with intact magic numbers.
# ---------------------------------------------------------------------------


class TestSvgRevalidationIdempotency:
    def _process(self, data: bytes) -> bytes:
        f = _make_upload(data, "logo.svg", "image/svg+xml")
        result = validate_and_process_logo(f)
        result.file.seek(0)
        return result.file.read()

    def test_sanitised_svg_root_keeps_unprefixed_svg_tag(self):
        # The default namespace must be preserved so the root stays <svg>,
        # not <ns0:svg>.
        out = self._process(_make_svg_bytes())
        assert out.lstrip().startswith(b"<svg")
        assert b"<ns0:svg" not in out

    def test_svg_survives_second_validation_pass(self):
        # The crux: re-validating an already-processed SVG must not raise.
        first = self._process(_make_svg_bytes())
        second = self._process(first)  # would raise ValidationError before fix
        assert second.lstrip().startswith(b"<svg")

    def test_svg_round_trip_is_idempotent(self):
        first = self._process(_make_svg_bytes())
        second = self._process(first)
        assert first == second

    def test_legacy_ns0_prefixed_svg_still_validates(self):
        # Records sanitised before the fix carry an "<ns0:svg ...>" root on disk;
        # re-saving them must not be rejected as an unsupported file type.
        legacy = (
            b'<ns0:svg xmlns:ns0="http://www.w3.org/2000/svg" '
            b'width="10" height="10"><ns0:rect width="10" height="10"/>'
            b"</ns0:svg>"
        )
        out = self._process(legacy)
        assert out.lstrip().startswith(b"<svg")

    def test_legacy_ns0_svg_is_normalised_to_unprefixed_on_resave(self):
        legacy = b'<ns0:svg xmlns:ns0="http://www.w3.org/2000/svg"></ns0:svg>'
        out = self._process(legacy)
        assert b"<ns0:svg" not in out


# ---------------------------------------------------------------------------
# XML attack prevention (stdlib ET safe on Python 3.12+ / Expat 2.7.1)
# ---------------------------------------------------------------------------


class TestXmlAttackPrevention:
    def _try_svg(self, svg_bytes: bytes):
        f = _make_upload(svg_bytes, "logo.svg", "image/svg+xml")
        with pytest.raises((ValidationError, Exception)):
            validate_and_process_logo(f)

    def test_xxe_payload_rejected(self):
        xxe = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<svg xmlns="http://www.w3.org/2000/svg">&xxe;</svg>'
        )
        self._try_svg(xxe)

    def test_expat_version_is_safe_against_entity_expansion(self):
        """
        Billion-laughs / entity-expansion attacks are mitigated by Expat itself
        in version 2.4.1+. Python 3.12 bundles Expat 2.7.4, well above that threshold.
        We assert the version here so CI will catch any regression if the bundled
        Expat is ever downgraded below the safe baseline.
        """
        import pyexpat
        from packaging.version import Version

        expat_ver = pyexpat.EXPAT_VERSION.split("_")[1]  # "expat_2.7.4" -> "2.7.4"
        assert Version(expat_ver) >= Version("2.4.1"), (
            f"Expat {expat_ver} is below 2.4.1 — billion-laughs protection not guaranteed"
        )


# ---------------------------------------------------------------------------
# Path traversal prevention
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_original_filename_not_used_in_storage_path(self):
        """
        _logo_upload_to() in models.py always generates a UUID path.
        The original filename is discarded after validate_and_process_logo().
        """
        from apps.submissions.models import _logo_upload_to

        class FakeInstance:
            pass

        path = _logo_upload_to(FakeInstance(), "../../etc/passwd.png")
        assert path.startswith("logos/")
        assert "passwd" not in path
        assert ".." not in path

    def test_uuid_paths_are_unique(self):
        from apps.submissions.models import _logo_upload_to

        class FakeInstance:
            pass

        paths = {_logo_upload_to(FakeInstance(), "logo.png") for _ in range(10)}
        assert len(paths) == 10  # All unique


# ---------------------------------------------------------------------------
# SVG content rules: root element, element allowlist, CSS references
# ---------------------------------------------------------------------------

_SVG_NS = "http://www.w3.org/2000/svg"
_XHTML_NS = "http://www.w3.org/1999/xhtml"


def _svg(body: str, extra_ns: str = "") -> str:
    return f'<svg xmlns="{_SVG_NS}"{extra_ns} width="10" height="10">{body}</svg>'


def _process(svg: str) -> str:
    f = _make_upload(svg.encode("utf-8"), "logo.svg", "image/svg+xml")
    result = validate_and_process_logo(f)
    result.file.seek(0)
    return result.file.read().decode("utf-8")


class TestSvgRootElement:
    def test_non_svg_root_is_rejected(self):
        doc = f'<?xml version="1.0"?><html xmlns="{_XHTML_NS}"><body/></html>'
        with pytest.raises(ValidationError, match="SVG"):
            _process(doc)

    def test_svg_root_without_namespace_is_rejected(self):
        with pytest.raises(ValidationError, match="SVG"):
            _process('<svg width="10" height="10"><rect width="1" height="1"/></svg>')


class TestSvgElementAllowlist:
    def test_foreign_object_and_its_content_are_removed(self):
        out = _process(
            _svg(
                f'<foreignObject width="5" height="5"><div xmlns="{_XHTML_NS}">'
                f'<p>html text</p></div></foreignObject><rect width="1" height="1"/>'
            )
        )
        assert "foreignObject" not in out
        assert "html text" not in out and "<p" not in out
        assert "<rect" in out

    def test_xhtml_elements_outside_foreign_object_are_removed(self):
        out = _process(_svg(f'<h:p xmlns:h="{_XHTML_NS}">html text</h:p><rect/>'))
        assert "html text" not in out and "<rect" in out

    @pytest.mark.parametrize(
        "element",
        [
            '<animate attributeName="opacity" values="0;1"/>',
            '<set attributeName="opacity" to="0"/>',
            '<animateTransform attributeName="transform" type="rotate"/>',
            '<animateMotion path="M0,0 L1,1"/>',
        ],
    )
    def test_animation_elements_are_removed(self, element):
        out = _process(_svg(f"<a>{element}<text>t</text></a>"))
        assert "animate" not in out and "<set" not in out
        assert "<text>t</text>" in out

    def test_non_svg_namespace_elements_are_removed(self):
        sodipodi = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
        out = _process(
            _svg(
                '<sodipodi:namedview id="nv"/><rect width="1" height="1"/>',
                extra_ns=f' xmlns:sodipodi="{sodipodi}"',
            )
        )
        assert "namedview" not in out
        assert "<rect" in out


class TestSvgCssReferences:
    def test_external_url_in_style_element_is_neutralised(self):
        out = _process(
            _svg("<style>rect{fill:url(https://example.org/x)}</style><rect/>")
        )
        assert "example.org" not in out
        assert "<style>" in out

    def test_import_rule_in_style_element_is_removed(self):
        out = _process(
            _svg("<style>@import url(https://example.org/a.css);rect{fill:red}</style>")
        )
        assert "@import" not in out and "example.org" not in out
        assert "rect{fill:red}" in out

    def test_external_url_in_style_attribute_is_neutralised(self):
        out = _process(
            _svg("<rect style=\"fill:url('https://example.org/x');stroke:red\"/>")
        )
        assert "example.org" not in out
        assert "stroke:red" in out

    def test_fragment_url_is_kept(self):
        out = _process(
            _svg('<style>.a{fill:url(#g)}</style><rect style="fill:url( #g )"/>')
        )
        assert "url(#g)" in out
        assert "url( #g )" in out

    def test_embedded_data_font_is_kept(self):
        css = "@font-face{font-family:F;src:url(data:font/ttf;base64,AAAA)}"
        out = _process(_svg(f"<style>{css}</style>"))
        assert "url(data:font/ttf;base64,AAAA)" in out


class TestSvgDesignToolExportsPreserved:
    """Typical Inkscape / Illustrator / Figma output keeps every element that
    affects how the logo looks."""

    EXPORT = _svg(
        "<title>Logo</title><desc>d</desc>"
        "<defs>"
        "<style>.cls-1{fill:url(#lg);}.cls-2{clip-path:url(#cp);}</style>"
        '<linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#fff"/>'
        "</linearGradient>"
        '<radialGradient id="rg" cx="5" cy="5" r="5" xlink:href="#lg"/>'
        '<clipPath id="cp"><rect width="10" height="10"/></clipPath>'
        '<mask id="m"><rect width="10" height="10" fill="#fff"/></mask>'
        '<pattern id="p" width="2" height="2"><circle cx="1" cy="1" r="1"/></pattern>'
        '<filter id="f"><feGaussianBlur stdDeviation="1"/><feOffset dx="1"/>'
        '<feFlood flood-color="#000"/><feComposite operator="in"/>'
        '<feColorMatrix type="saturate" values="0"/>'
        "<feMerge><feMergeNode/></feMerge></filter>"
        '<symbol id="s"><path d="M0 0h1v1z"/></symbol>'
        '<marker id="mk"><path d="M0 0"/></marker>'
        "</defs>"
        '<metadata id="md"/>'
        '<g class="cls-2" filter="url(#f)" transform="translate(1 1)">'
        '<path class="cls-1" d="M0 0h10v10H0z"/>'
        '<circle cx="5" cy="5" r="2"/><ellipse cx="5" cy="5" rx="2" ry="1"/>'
        '<line x1="0" y1="0" x2="1" y2="1"/><polyline points="0,0 1,1"/>'
        '<polygon points="0,0 1,1 1,0"/>'
        '<text x="1" y="9" xml:space="preserve"><tspan>de.NBI</tspan></text>'
        '<use xlink:href="#s"/><use href="#s"/>'
        "</g>",
        extra_ns=' xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10"',
    )

    def test_round_trip_keeps_every_visual_element(self):
        import xml.etree.ElementTree as ET

        def tags(doc):
            return sorted(e.tag for e in ET.fromstring(doc).iter())

        assert tags(_process(self.EXPORT)) == tags(self.EXPORT)

    def test_round_trip_keeps_references_and_text(self):
        out = _process(self.EXPORT)
        for kept in ("url(#lg)", "url(#cp)", 'filter="url(#f)"', "de.NBI", 'href="#s"'):
            assert kept in out


class TestSvgCssEscapes:
    def test_escaped_characters_in_style_element_are_rejected(self):
        with pytest.raises(ValidationError, match="escape"):
            _process(_svg("<style>rect{fill:\\75 rl(x)}</style>"))

    def test_escaped_characters_in_style_attribute_are_rejected(self):
        with pytest.raises(ValidationError, match="escape"):
            _process(_svg('<rect style="fill:\\75 rl(x)"/>'))


class TestSvgRemovalKeepsSurroundingText:
    """Removing an element must not drop the text that follows it (Visio
    exports put metadata elements inside <text> before the visible text)."""

    VISIO = ' xmlns:v="http://schemas.microsoft.com/visio/2003/SVGExtensions/"'

    def test_text_after_removed_first_child_is_kept(self):
        out = _process(
            _svg("<text><v:paragraph/><v:tabList/>Visible label</text>", self.VISIO)
        )
        assert "paragraph" not in out and "tabList" not in out
        assert "<text>Visible label</text>" in out

    def test_text_after_removed_middle_child_is_kept(self):
        out = _process(_svg("<text>A<tspan>B</tspan><v:tabList/>C</text>", self.VISIO))
        assert "<text>A<tspan>B</tspan>C</text>" in out


class TestSvgReferencesInAllForms:
    """References are limited to #fragment / data: wherever CSS is parsed:
    <style>, the style attribute and presentation attributes."""

    @pytest.mark.parametrize("attr", ["fill", "stroke", "filter", "clip-path", "mask"])
    def test_external_url_in_presentation_attribute_is_neutralised(self, attr):
        out = _process(_svg(f'<rect {attr}="url(https://example.org/x)"/>'))
        assert "example.org" not in out

    def test_fragment_url_in_presentation_attribute_is_kept(self):
        out = _process(_svg('<g clip-path="url(#cp)"/>'))
        assert 'clip-path="url(#cp)"' in out

    def test_unclosed_external_url_is_rejected(self):
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg("<style>rect{fill:url(https://example.org/x</style>"))

    def test_unclosed_fragment_url_is_rejected(self):
        # Malformed CSS around a reference is rejected rather than interpreted.
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg("<style>rect{fill:url(#g</style>"))

    @pytest.mark.parametrize(
        "css",
        [
            'rect{fill:image-set("https://example.org/x" 1x)}',
            'rect{fill:-webkit-image-set("https://example.org/x" 1x)}',
            'rect{fill:src("https://example.org/x")}',
        ],
    )
    def test_string_image_functions_are_rejected(self, css):
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg(f"<style>{css}</style>"))

    def test_escaped_characters_in_presentation_attribute_are_rejected(self):
        with pytest.raises(ValidationError, match="escape"):
            _process(_svg('<rect fill="\\75 rl(x)"/>'))

    def test_editor_attribute_with_backslash_is_dropped_not_rejected(self):
        ink = ' xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
        out = _process(_svg('<g inkscape:export-filename="C:\\logo.png"/>', ink))
        assert "export-filename" not in out and "C:" not in out


class TestSvgNamespacedAttributes:
    """Only xlink:href (fragment links), xml:space and xml:lang are kept from
    namespaced attributes; everything else is dropped."""

    S = ' xmlns:s="http://www.w3.org/2000/svg"'

    def _reparses(self, out: str) -> None:
        import xml.etree.ElementTree as ET

        ET.fromstring(out)  # raises on invalid XML (e.g. duplicate attributes)

    def test_svg_prefixed_style_with_external_url_is_dropped(self):
        out = _process(
            _svg('<rect s:style="fill:url(https://example.org/x)"/>', self.S)
        )
        assert "example.org" not in out
        self._reparses(out)

    def test_svg_prefixed_style_with_escape_is_dropped(self):
        out = _process(_svg('<rect s:style="fill:\\75rl(x)"/>', self.S))
        assert "\\" not in out

    def test_svg_prefixed_duplicate_attribute_keeps_valid_xml(self):
        out = _process(_svg('<rect fill="red" s:fill="blue"/>', self.S))
        self._reparses(out)
        assert 'fill="red"' in out and "blue" not in out

    def test_xml_base_is_dropped(self):
        out = _process(_svg('<g xml:base="https://example.org/"><rect/></g>'))
        assert "example.org" not in out

    def test_editor_attributes_are_dropped(self):
        ink = ' xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"'
        out = _process(_svg('<g inkscape:label="Layer 1" id="l1"><rect/></g>', ink))
        assert "Layer 1" not in out and 'id="l1"' in out

    def test_xml_space_and_lang_are_kept(self):
        out = _process(_svg('<text xml:space="preserve" xml:lang="de">a  b</text>'))
        assert 'xml:space="preserve"' in out and 'xml:lang="de"' in out

    def test_xlink_fragment_href_is_kept(self):
        xl = ' xmlns:xlink="http://www.w3.org/1999/xlink"'
        out = _process(_svg('<use xlink:href="#a"/>', xl))
        assert 'href="#a"' in out

    def test_ping_attribute_is_dropped(self):
        out = _process(_svg('<a href="#x" ping="https://example.org/p"><rect/></a>'))
        assert "example.org" not in out


class TestSvgNesting:
    def test_deeply_nested_svg_is_rejected_with_a_message(self):
        svg = _svg("<g>" * 5000 + "</g>" * 5000)
        with pytest.raises(ValidationError, match="nested"):
            _process(svg)

    def test_normal_nesting_is_accepted(self):
        out = _process(_svg("<g>" * 50 + "<rect/>" + "</g>" * 50))
        assert "<rect" in out


class TestSvgCssScaling:
    """CSS reference handling must scale linearly with input size: 4x the
    input may cost at most ~10x the time (quadratic would be 16x)."""

    @staticmethod
    def _cost(css: str, stylesheet: bool) -> float:
        """Best of 5 runs, with garbage collection paused while timing."""
        import gc
        import time

        from apps.submissions.logo_utils import _clean_css

        best = float("inf")
        for _ in range(5):
            gc.collect()
            gc.disable()
            try:
                start = time.perf_counter()
                try:
                    _clean_css(css, stylesheet=stylesheet)
                except ValidationError:
                    pass
                best = min(best, time.perf_counter() - start)
            finally:
                gc.enable()
        return best

    @pytest.mark.parametrize(
        "make",
        [
            lambda n: "url(" + " " * n,
            lambda n: "url('" + " " * n,
            lambda n: "url(#a" + " " * n,
            lambda n: "url(" * (n // 4),
            lambda n: "url(#a) " * (n // 8),
            lambda n: "@import " * (n // 8),
        ],
        ids=[
            "unclosed-spaces",
            "unclosed-quote",
            "fragment-spaces",
            "repeated-open",
            "repeated-fragment",
            "repeated-import",
        ],
    )
    @pytest.mark.parametrize("stylesheet", [False, True], ids=["attribute", "style"])
    def test_scales_linearly(self, make, stylesheet):
        small = self._cost(make(50_000), stylesheet)
        large = self._cost(make(200_000), stylesheet)
        assert large < 10 * max(small, 1e-4)


class TestSvgCssParsing:
    """CSS in SVG logos is read with a CSS Syntax-spec tokenizer (tinycss2)."""

    def test_css_without_changes_is_kept_byte_for_byte(self):
        css = ".a{fill:url( '#g' )}  /* note */ .b{font-family:'Open Sans'}"
        out = _process(_svg(f"<style>{css}</style>"))
        assert f"<style>{css}</style>" in out

    def test_quoted_data_reference_is_kept(self):
        css = "@font-face{font-family:F;src:url('data:font/ttf;base64,AA')}"
        out = _process(_svg(f"<style>{css}</style>"))
        assert css in out

    def test_uppercase_external_url_is_neutralised(self):
        out = _process(_svg("<style>rect{fill:URL(https://example.org/x)}</style>"))
        assert "example.org" not in out

    def test_quoted_external_url_is_neutralised(self):
        out = _process(_svg('<style>rect{fill:url( "https://example.org/x" )}</style>'))
        assert "example.org" not in out
        assert "rect{fill:none}" in out

    @pytest.mark.parametrize(
        "css",
        [
            'rect{fill:url("https://example.org/x)}',  # unclosed string
            "rect{fill:url(https://example.org/x y)}",  # bad url
            'rect{fill:url("#a" x)}',  # extra argument
            'rect{fill:url("https://ex\nample.org/x")}',  # newline in string
        ],
    )
    def test_malformed_reference_is_rejected(self, css):
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg(f"<style>{css}</style>"))

    def test_image_function_is_rejected(self):
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg('<style>rect{fill:image("https://example.org/x")}</style>'))

    def test_import_nested_in_at_rule_is_rejected(self):
        with pytest.raises(ValidationError, match="reference"):
            _process(_svg("<style>@media screen { @import 'x.css'; }</style>"))

    def test_attribute_text_without_references_is_not_parsed(self):
        # An apostrophe is an unclosed string in CSS terms; plain text without
        # any reference must not be rejected for that.
        out = _process(_svg('<g aria-label="Don\'t panic"><rect/></g>'))
        assert "Don't panic" in out

    def test_deeply_nested_css_needing_a_change_gives_a_message(self):
        css = "rect{fill:" + "(" * 5000 + "url(https://example.org/x)}"
        with pytest.raises(ValidationError):
            _process(_svg(f"<style>{css}</style>"))


class TestSvgPruningScaling:
    """Removing elements must scale linearly with the number of elements:
    4x the input may cost at most ~10x the time (quadratic would be 16x)."""

    SVG_NS = "http://www.w3.org/2000/svg"

    @staticmethod
    def _cost(svg: str) -> float:
        """Best of 3 runs, with garbage collection paused while timing."""
        import gc
        import time

        best = float("inf")
        for _ in range(3):
            f = _make_upload(svg.encode(), "logo.svg", "image/svg+xml")
            gc.collect()
            gc.disable()
            try:
                start = time.perf_counter()
                validate_and_process_logo(f)
                best = min(best, time.perf_counter() - start)
            finally:
                gc.enable()
        return best

    @pytest.mark.parametrize(
        "body",
        [
            # many kept siblings followed by many removed ones
            lambda n: "<g/>" * n + "<x:a/>" * n,
            # many removed siblings, each followed by text
            lambda n: "<text>" + "<x:a/>t" * n + "</text>",
            # many children inside <style>
            lambda n: "<style>" + "<g/>" * n + "</style>",
        ],
        ids=["kept-then-removed", "removed-with-text", "style-children"],
    )
    def test_scales_linearly(self, body, settings):
        settings.LOGO_MAX_SVG_BYTES = 10 * 1024 * 1024

        def svg(n):
            return f'<svg xmlns="{self.SVG_NS}" xmlns:x="urn:x"><g>{body(n)}</g></svg>'

        small, large = self._cost(svg(5_000)), self._cost(svg(20_000))
        assert large < 10 * max(small, 1e-4)
