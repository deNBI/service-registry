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
