"""Bounded, dependency-free parsing for uploaded HTML dashboard bundles.

This module validates package bytes only. It does not decide authorization,
storage, or where the resulting assets are served. Callers must serve validated
HTML from an isolated origin with an appropriate browser sandbox.
"""

from __future__ import annotations

import hashlib
import html.parser
import io
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
"""Largest accepted input value, including ZIP headers and directory records."""

MAX_ARCHIVE_ENTRIES = 256
"""Largest number of ZIP central-directory records, including directories."""

MAX_ASSET_BYTES = 4 * 1024 * 1024
"""Largest expanded size for any one asset."""

MAX_EXPANDED_BYTES = 16 * 1024 * 1024
"""Largest combined expanded size of ZIP file entries."""

MAX_COMPRESSION_RATIO = 100
"""Largest allowed expanded-to-compressed size ratio, per file and in total."""

MAX_PATH_BYTES = 512
MAX_SEGMENT_BYTES = 255
_CHUNK_BYTES = 64 * 1024
_EOCD_SEARCH_BYTES = 22 + 0xFFFF
_EOCD_STRUCT = struct.Struct("<4s4H2LH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_ZIP_SUFFIX = ".zip"
_UNSAFE_PATH_CHARACTERS = frozenset('\\:;?#%<>"|*')
_CONFUSABLE_SEPARATORS = frozenset(
    {
        "\u2024",  # one dot leader
        "\u2044",  # fraction slash
        "\u2215",  # division slash
        "\u2216",  # set minus, commonly drawn like a backslash
        "\u27cb",  # mathematical rising diagonal
        "\u2571",  # box drawings light diagonal upper right to lower left
        "\u2572",  # box drawings light diagonal upper left to lower right
        "\u29f8",  # big solidus
        "\u29f5",  # reverse solidus operator
        "\u29f9",  # big reverse solidus
        "\ufe68",  # small reverse solidus
        "\ufe52",  # small full stop
        "\uff0f",  # fullwidth solidus
        "\uff3c",  # fullwidth reverse solidus
        "\uff0e",  # fullwidth full stop
        "\u3002",  # ideographic full stop
        "\ufe0e",  # text variation selector
        "\ufe0f",  # emoji variation selector
    }
)
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
    | {f"{device}{number}" for device in ("com", "lpt") for number in "¹²³"}
)
_ZIP_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


class BundleValidationError(ValueError):
    """A rejected upload with a stable machine code and HTTP status suggestion."""

    def __init__(self, code: str, message: str, *, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class BundleAsset:
    """One validated package file, with a SHA-256 hex digest of ``content``."""

    path: str
    content: bytes
    mime: str
    digest: str

    @property
    def sha256(self) -> str:
        """SHA-256 hex digest alias for persistence contracts using that name."""

        return self.digest


@dataclass(frozen=True, slots=True)
class ValidatedBundle:
    """Validated immutable-in-memory assets and the one HTML entry page."""

    entry_path: str
    assets: tuple[BundleAsset, ...]


def parse_bundle(upload_name: str, payload: bytes) -> ValidatedBundle:
    """Parse one uploaded ``.html`` file or ``.zip`` package.

    ZIP packages must contain exactly one ``.html`` or ``.htm`` file. ZIP asset
    paths are preserved using normalized POSIX separators. The returned bundle
    contains at most 16 MiB of expanded asset bytes.
    """

    if not isinstance(upload_name, str) or not isinstance(payload, bytes):
        raise BundleValidationError(
            "invalid_upload", "Upload name and contents must be text and bytes."
        )
    if len(payload) > MAX_UPLOAD_BYTES:
        raise BundleValidationError(
            "upload_too_large",
            f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit.",
            http_status=413,
        )

    suffix = upload_name.rsplit(".", 1)[-1].casefold() if "." in upload_name else ""
    if suffix in {extension[1:] for extension in _HTML_SUFFIXES}:
        path = _validate_path(upload_name)
        if "/" in path:
            raise BundleValidationError(
                "unsafe_path", "A single HTML upload must use a plain file name."
            )
        if len(payload) > MAX_ASSET_BYTES:
            raise BundleValidationError(
                "asset_too_large",
                f"The HTML entry exceeds the {MAX_ASSET_BYTES // (1024 * 1024)} MiB per-file limit.",
                http_status=413,
            )
        _validate_html(payload)
        return ValidatedBundle(entry_path=path, assets=(_make_asset(path, payload),))

    if f".{suffix}" != _ZIP_SUFFIX:
        raise BundleValidationError(
            "unsupported_file_type", "Upload one .html file or one .zip package."
        )

    return _parse_zip(payload)


def _parse_zip(payload: bytes) -> ValidatedBundle:
    try:
        _preflight_entry_count(payload)
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
            infos = archive.infolist()
            if not infos:
                raise BundleValidationError(
                    "empty_archive", "ZIP package does not contain any files."
                )
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise BundleValidationError(
                    "too_many_entries",
                    f"ZIP package exceeds the {MAX_ARCHIVE_ENTRIES} entry limit.",
                    http_status=413,
                )

            prepared: list[tuple[zipfile.ZipInfo, str]] = []
            explicit_paths: dict[str, tuple[str, bool]] = {}
            prefix_spellings: dict[str, str] = {}
            file_keys: set[str] = set()
            directory_keys: set[str] = set()
            declared_expanded = 0
            declared_compressed = 0

            for info in infos:
                raw_name = getattr(info, "orig_filename", info.filename)
                raw_name = _validate_raw_name(raw_name)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                dos_attributes = info.external_attr & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                is_directory = raw_name.endswith("/")

                if info.flag_bits & 0x41:
                    raise BundleValidationError(
                        "encrypted_entry", "Encrypted ZIP entries are not supported."
                    )
                if file_type == stat.S_IFLNK:
                    raise BundleValidationError(
                        "unsupported_entry", "Symbolic links are not supported in ZIP packages."
                    )
                if dos_attributes & 0x400:
                    raise BundleValidationError(
                        "unsupported_entry", "ZIP reparse points are not supported."
                    )
                if not is_directory and dos_attributes & 0x10:
                    raise BundleValidationError(
                        "unsupported_entry", "ZIP directory entries must end in a slash."
                    )
                if info.compress_type not in _ZIP_COMPRESSION_METHODS:
                    raise BundleValidationError(
                        "unsupported_compression",
                        "ZIP package must use stored or deflate compression.",
                    )
                if is_directory:
                    if file_type not in (0, stat.S_IFDIR):
                        raise BundleValidationError(
                            "unsupported_entry", "ZIP package contains a non-file entry."
                        )
                    if info.file_size != 0 or (
                        info.compress_size > 0
                        and not (
                            info.compress_type == zipfile.ZIP_DEFLATED
                            and info.compress_size <= 2
                        )
                    ):
                        raise BundleValidationError(
                            "unsupported_entry", "ZIP directory records must be empty."
                        )
                    path = _validate_path(raw_name[:-1])
                    directory_keys.add(path.casefold())
                else:
                    if file_type not in (0, stat.S_IFREG):
                        raise BundleValidationError(
                            "unsupported_entry", "ZIP package contains a non-regular file."
                        )
                    path = _validate_path(raw_name)
                    if info.file_size < 0 or info.compress_size < 0:
                        raise BundleValidationError(
                            "invalid_zip", "ZIP package contains invalid size metadata."
                        )
                    if info.file_size > MAX_ASSET_BYTES:
                        raise BundleValidationError(
                            "asset_too_large",
                            f"An asset exceeds the {MAX_ASSET_BYTES // (1024 * 1024)} MiB per-file limit.",
                            http_status=413,
                        )
                    declared_expanded += info.file_size
                    declared_compressed += info.compress_size
                    if declared_expanded > MAX_EXPANDED_BYTES:
                        raise BundleValidationError(
                            "expanded_too_large",
                            f"ZIP package exceeds the {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB expanded-size limit.",
                            http_status=413,
                        )
                    _check_compression_ratio(info.file_size, info.compress_size)
                    file_keys.add(path.casefold())

                key = path.casefold()
                previous = explicit_paths.get(key)
                if previous is not None:
                    old_path, old_is_directory = previous
                    if old_path != path or old_is_directory != is_directory:
                        raise BundleValidationError(
                            "path_collision",
                            "ZIP package has paths that collide by case or file/directory name.",
                        )
                    raise BundleValidationError(
                        "duplicate_path", "ZIP package contains the same path more than once."
                    )
                explicit_paths[key] = (path, is_directory)

                for prefix in _path_prefixes(path):
                    folded_prefix = prefix.casefold()
                    previous_spelling = prefix_spellings.get(folded_prefix)
                    if previous_spelling is not None and previous_spelling != prefix:
                        raise BundleValidationError(
                            "path_collision",
                            "ZIP package has paths that collide by case or file/directory name.",
                        )
                    prefix_spellings[folded_prefix] = prefix

                prepared.append((info, path))

            if any(
                parent in file_keys
                for file_path in (path for path, is_directory in explicit_paths.values() if not is_directory)
                for parent in _path_prefix_keys(file_path)
            ):
                raise BundleValidationError(
                    "path_collision", "A ZIP file path is also used as a directory."
                )
            if file_keys & directory_keys:
                raise BundleValidationError(
                    "path_collision", "A ZIP file path is also used as a directory."
                )
            _check_compression_ratio(declared_expanded, declared_compressed)

            html_paths = [
                path
                for info, path in prepared
                if not info.is_dir() and _suffix(path) in _HTML_SUFFIXES
            ]
            if not html_paths:
                raise BundleValidationError(
                    "missing_entry", "ZIP package must contain one .html or .htm entry file."
                )
            if len(html_paths) != 1:
                raise BundleValidationError(
                    "ambiguous_entry", "ZIP package must contain exactly one HTML entry file."
                )
            entry_path = html_paths[0]

            assets: list[BundleAsset] = []
            actual_expanded = 0
            for info, path in prepared:
                if info.is_dir():
                    continue
                content = _read_entry(archive, info, actual_expanded)
                actual_expanded += len(content)
                if actual_expanded > MAX_EXPANDED_BYTES:
                    raise BundleValidationError(
                        "expanded_too_large",
                        f"ZIP package exceeds the {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB expanded-size limit.",
                        http_status=413,
                    )
                if len(content) != info.file_size:
                    raise BundleValidationError(
                        "invalid_zip", "ZIP entry size does not match its directory metadata."
                    )
                if path == entry_path:
                    _validate_html(content)
                assets.append(_make_asset(path, content))

            assets.sort(key=lambda asset: asset.path)
            return ValidatedBundle(entry_path=entry_path, assets=tuple(assets))
    except BundleValidationError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        zlib.error,
    ) as exc:
        raise BundleValidationError(
            "invalid_zip", "ZIP package is corrupt or uses unsupported compression."
        ) from exc


def _preflight_entry_count(payload: bytes) -> None:
    """Reject normal high-entry-count archives before ZipFile builds ZipInfo objects."""

    tail_start = max(0, len(payload) - _EOCD_SEARCH_BYTES)
    tail = payload[tail_start:]
    position = tail.rfind(_EOCD_SIGNATURE)
    if position < 0 or position + _EOCD_STRUCT.size > len(tail):
        return  # ZipFile will report the malformed/missing end record.
    fields = _EOCD_STRUCT.unpack_from(tail, position)
    comment_length = fields[-1]
    if position + _EOCD_STRUCT.size + comment_length > len(tail):
        return
    total_entries = fields[4]
    if total_entries > MAX_ARCHIVE_ENTRIES:
        raise BundleValidationError(
            "too_many_entries",
            f"ZIP package exceeds the {MAX_ARCHIVE_ENTRIES} entry limit.",
            http_status=413,
        )


def _read_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, prior_expanded: int) -> bytes:
    content = bytearray()
    max_read = min(MAX_ASSET_BYTES, MAX_EXPANDED_BYTES - prior_expanded)
    try:
        with archive.open(info, mode="r") as entry:
            while True:
                remaining_with_overflow_probe = max_read - len(content) + 1
                if remaining_with_overflow_probe <= 0:
                    raise BundleValidationError(
                        "expanded_too_large",
                        f"ZIP package exceeds the {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB expanded-size limit.",
                        http_status=413,
                    )
                chunk = entry.read(min(_CHUNK_BYTES, remaining_with_overflow_probe))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_ASSET_BYTES:
                    raise BundleValidationError(
                        "asset_too_large",
                        f"An asset exceeds the {MAX_ASSET_BYTES // (1024 * 1024)} MiB per-file limit.",
                        http_status=413,
                    )
                if len(content) + prior_expanded > MAX_EXPANDED_BYTES:
                    raise BundleValidationError(
                        "expanded_too_large",
                        f"ZIP package exceeds the {MAX_EXPANDED_BYTES // (1024 * 1024)} MiB expanded-size limit.",
                        http_status=413,
                    )
    except BundleValidationError:
        raise
    except (
        zipfile.BadZipFile,
        EOFError,
        OSError,
        RuntimeError,
        NotImplementedError,
        zlib.error,
    ) as exc:
        raise BundleValidationError(
            "invalid_zip", "ZIP package is corrupt or could not be decompressed."
        ) from exc
    return bytes(content)


def _check_compression_ratio(expanded: int, compressed: int) -> None:
    if expanded == 0:
        return
    if compressed <= 0 or expanded > compressed * MAX_COMPRESSION_RATIO:
        raise BundleValidationError(
            "compression_ratio_exceeded",
            f"ZIP package exceeds the {MAX_COMPRESSION_RATIO}:1 compression-ratio limit.",
            http_status=413,
        )


def _validate_raw_name(raw_name: object) -> str:
    if not isinstance(raw_name, str) or not raw_name:
        raise BundleValidationError("unsafe_path", "ZIP package contains an empty path.")
    if "\x00" in raw_name:
        raise BundleValidationError("unsafe_path", "ZIP paths cannot contain NUL characters.")
    if "\\" in raw_name:
        raise BundleValidationError(
            "unsafe_path", "ZIP paths must use forward-slash separators."
        )
    if raw_name.startswith("/"):
        raise BundleValidationError("unsafe_path", "ZIP paths must be relative.")
    return raw_name


def _validate_path(path: str) -> str:
    if not path or path.startswith("/") or path.endswith("/"):
        raise BundleValidationError("unsafe_path", "Package paths must be relative file paths.")
    if unicodedata.normalize("NFC", path) != path:
        raise BundleValidationError(
            "unsafe_path", "Package paths must use Unicode NFC normalization."
        )
    try:
        encoded_path = path.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise BundleValidationError("unsafe_path", "Package path is not valid Unicode.") from exc
    if len(encoded_path) > MAX_PATH_BYTES:
        raise BundleValidationError(
            "unsafe_path", f"Package paths cannot exceed {MAX_PATH_BYTES} UTF-8 bytes."
        )

    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise BundleValidationError(
            "unsafe_path", "Package paths cannot contain empty, dot, or parent segments."
        )
    for segment in segments:
        try:
            segment_bytes = segment.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise BundleValidationError(
                "unsafe_path", "Package path is not valid Unicode."
            ) from exc
        if len(segment_bytes) > MAX_SEGMENT_BYTES:
            raise BundleValidationError(
                "unsafe_path",
                f"Package path components cannot exceed {MAX_SEGMENT_BYTES} UTF-8 bytes.",
            )
        if segment.endswith(".") or segment[-1].isspace():
            raise BundleValidationError(
                "unsafe_path", "Package path components cannot end in a space or dot."
            )
        if segment.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES:
            raise BundleValidationError(
                "unsafe_path", "Package paths cannot use reserved Windows device names."
            )
        for character in segment:
            category = unicodedata.category(character)
            if (
                character in _UNSAFE_PATH_CHARACTERS
                or character in _CONFUSABLE_SEPARATORS
                or 0xE0100 <= ord(character) <= 0xE01EF
                or category.startswith("C")
                or category in {"Zl", "Zp"}
            ):
                raise BundleValidationError(
                    "unsafe_path", "Package path contains a reserved or ambiguous character."
                )

    return path


def _path_prefixes(path: str) -> tuple[str, ...]:
    segments = path.split("/")
    return tuple("/".join(segments[:index]) for index in range(1, len(segments) + 1))


def _path_prefix_keys(path: str) -> tuple[str, ...]:
    segments = path.split("/")
    return tuple("/".join(segments[:index]).casefold() for index in range(1, len(segments)))


def _validate_html(content: bytes) -> None:
    try:
        document = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise BundleValidationError(
            "invalid_html_encoding", "HTML entry must be encoded as UTF-8."
        ) from exc

    scanner = _HtmlRiskScanner()
    scanner.feed(document)
    scanner.close()
    if scanner.problem is not None:
        raise BundleValidationError("unsupported_html_navigation", scanner.problem)


class _HtmlRiskScanner(html.parser.HTMLParser):
    """Reject a few static navigation escapes the isolated viewer will not allow."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problem: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.problem is not None:
            return
        self._check_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.problem is not None:
            return
        self._check_tag(tag, attrs)

    def _check_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values: dict[str, str] = {}
        for name, value in attrs:
            values.setdefault(name.casefold(), value or "")
        lowered_tag = tag.casefold()
        if lowered_tag == "base":
            self.problem = "HTML base elements are not supported in an isolated package."
            return
        if lowered_tag == "meta" and values.get("http-equiv", "").strip().casefold() == "refresh":
            self.problem = "HTML meta refresh redirects are not supported."
            return
        if lowered_tag in {"a", "area", "form", "iframe", "frame"}:
            target = values.get("target", "").strip().casefold()
            if target in {"_top", "_parent"}:
                self.problem = "HTML navigation to the parent or top frame is not supported."
                return
        if lowered_tag in {"button", "input"}:
            target = values.get("formtarget", "").strip().casefold()
            if target in {"_top", "_parent"}:
                self.problem = "Form navigation to the parent or top frame is not supported."
                return

        for raw_name, raw_value in attrs:
            name = raw_name.casefold()
            value = raw_value or ""
            if name == "srcdoc" and lowered_tag == "iframe":
                self.problem = "Inline iframe documents are not supported in uploaded HTML."
                return
            if not value or name not in {
                "href",
                "src",
                "action",
                "formaction",
                "xlink:href",
                "data",
            }:
                continue
            scheme = _url_scheme(value)
            if scheme in {"javascript", "vbscript"}:
                self.problem = "Script URL navigation is not supported in uploaded HTML."
                return
            if scheme == "data" and (
                name in {"href", "action", "formaction", "xlink:href"}
                or lowered_tag in {"iframe", "frame", "object", "embed", "script"}
            ):
                self.problem = "Data URL navigation and embedded documents are not supported."
                return


def _url_scheme(value: str) -> str | None:
    # Browsers discard ASCII controls and whitespace while parsing URL schemes.
    colon = value.find(":")
    if colon <= 0:
        return None
    scheme = "".join(character for character in value[:colon] if ord(character) > 0x20).casefold()
    if re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme):
        return scheme
    return None


def _suffix(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].casefold()


def _make_asset(path: str, content: bytes) -> BundleAsset:
    return BundleAsset(
        path=path,
        content=content,
        mime=_mime_for_path(path),
        digest=hashlib.sha256(content).hexdigest(),
    )


def _mime_for_path(path: str) -> str:
    """Return a fixed MIME map; never infer a response type from uploaded bytes."""

    suffix = _suffix(path)
    mimes = {
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".map": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".xml": "application/xml; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".avif": "image/avif",
        ".ico": "image/vnd.microsoft.icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".eot": "application/vnd.ms-fontobject",
        ".wasm": "application/wasm",
        ".pdf": "application/pdf",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }
    return mimes.get(suffix, "application/octet-stream")
