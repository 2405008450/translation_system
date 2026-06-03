from __future__ import annotations

import json
import random
import shutil
import socket
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.config import get_settings


class LibreOfficeError(RuntimeError):
    """LibreOffice 调用失败。"""


class LibreOfficeUnavailableError(LibreOfficeError):
    """当前环境没有可用的 LibreOffice。"""


WORD_DOCUMENT_EXTENSIONS = {".doc", ".docx"}

_WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_STATISTICS_SCRIPT = r"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
import unicodedata

import uno
from com.sun.star.beans import PropertyValue

NON_ASIAN_WORD_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[.,:/_-][A-Za-z0-9]+)*%?|[^\W_\d\s]+(?:[-'][^\W_\d\s]+)*",
    re.UNICODE,
)


def to_optional_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def first_optional_int(*values):
    for value in values:
        converted = to_optional_int(value)
        if converted is not None:
            return converted
    return None


def is_asian_count_char(char):
    if char.isspace() or char.isascii():
        return False
    return unicodedata.east_asian_width(char) in {"W", "F"}


def count_asian_characters(text):
    return sum(1 for char in text if is_asian_count_char(char))


def count_non_asian_words(text):
    non_asian_text = "".join(" " if is_asian_count_char(char) else char for char in text)
    return len(NON_ASIAN_WORD_PATTERN.findall(non_asian_text))


def read_named_statistics(document):
    result = {}
    try:
        statistics = document.getDocumentProperties().DocumentStatistics
    except Exception:
        return result
    for item in statistics:
        try:
            result[str(item.Name)] = to_optional_int(item.Value)
        except Exception:
            continue
    return result


def read_document_text(document):
    try:
        return document.Text.String or ""
    except Exception:
        return ""


def read_text_object_string(text_object):
    if text_object is None:
        return ""
    try:
        return text_object.String or ""
    except Exception:
        return ""


def append_text_part(parts, text):
    if text:
        parts.append(text)


def append_unique_text_part(parts, seen, text):
    if not text or text in seen:
        return
    seen.add(text)
    parts.append(text)


def read_named_text_collection(collection):
    parts = []
    try:
        names = collection.getElementNames()
    except Exception:
        return parts
    for name in names:
        try:
            item = collection.getByName(name)
        except Exception:
            continue
        append_text_part(parts, read_text_object_string(getattr(item, "Text", item)))
    return parts


def read_indexed_text_collection(collection):
    parts = []
    try:
        count = int(collection.getCount())
    except Exception:
        return parts
    for index in range(count):
        try:
            item = collection.getByIndex(index)
        except Exception:
            continue
        append_text_part(parts, read_text_object_string(getattr(item, "Text", item)))
    return parts


def read_page_style_texts(document):
    parts = []
    seen = set()
    text_properties = (
        ("HeaderIsOn", ("HeaderText", "HeaderTextLeft", "HeaderTextRight", "HeaderTextFirst")),
        ("FooterIsOn", ("FooterText", "FooterTextLeft", "FooterTextRight", "FooterTextFirst")),
    )
    try:
        page_styles = document.StyleFamilies.getByName("PageStyles")
        names = page_styles.getElementNames()
    except Exception:
        return parts

    for name in names:
        try:
            style = page_styles.getByName(name)
        except Exception:
            continue
        for enabled_property, text_property_names in text_properties:
            try:
                enabled = bool(getattr(style, enabled_property))
            except Exception:
                enabled = False
            if not enabled:
                continue
            for text_property_name in text_property_names:
                try:
                    text_object = getattr(style, text_property_name)
                except Exception:
                    continue
                append_unique_text_part(parts, seen, read_text_object_string(text_object))
    return parts


def read_counted_document_text(document):
    parts = []
    append_text_part(parts, read_document_text(document))

    for collection_name in ("TextFrames",):
        try:
            parts.extend(read_named_text_collection(getattr(document, collection_name)))
        except Exception:
            pass

    for collection_name in ("Footnotes", "Endnotes"):
        try:
            parts.extend(read_indexed_text_collection(getattr(document, collection_name)))
        except Exception:
            pass

    parts.extend(read_page_style_texts(document))
    return "\n".join(parts)


def count_word_count_spaces(text):
    return sum(1 for char in text if char.isspace() and char not in "\r\n")


def count_layout_lines(document):
    try:
        cursor = document.CurrentController.getViewCursor()
        cursor.gotoStart(False)
        count = 1
        while count < 200000 and cursor.goDown(1, False):
            count += 1
        return count
    except Exception:
        return None


def property_value(name, value):
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def connect_context(port):
    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver",
        local_context,
    )
    last_error = None
    for _ in range(40):
        try:
            return resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
            )
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"cannot connect to LibreOffice listener: {last_error}")


def main():
    port = int(sys.argv[1])
    source_path = sys.argv[2]
    context = connect_context(port)
    desktop = context.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
    document = None
    try:
        document = desktop.loadComponentFromURL(
            uno.systemPathToFileUrl(source_path),
            "_blank",
            0,
            (
                property_value("Hidden", True),
                property_value("ReadOnly", True),
                property_value("UpdateDocMode", 0),
            ),
        )
        if document is None:
            raise RuntimeError("LibreOffice returned no document")

        named = read_named_statistics(document)
        counted_text = read_counted_document_text(document)
        line_count = count_layout_lines(document)
        words = first_optional_int(getattr(document, "WordCount", None), named.get("WordCount"))
        characters_with_spaces = first_optional_int(
            getattr(document, "CharacterCount", None),
            named.get("CharacterCount"),
        )
        space_count = count_word_count_spaces(counted_text)
        asian_characters = count_asian_characters(counted_text)
        if words is None:
            non_asian_words = count_non_asian_words(counted_text)
        else:
            non_asian_words = max(words - asian_characters, 0)
        if characters_with_spaces is None:
            characters = to_optional_int(named.get("NonWhitespaceCharacterCount"))
        else:
            characters = max(characters_with_spaces - space_count, 0)
        try:
            live_page_count = getattr(document.CurrentController, "PageCount", None)
        except Exception:
            live_page_count = None
        payload = {
            "pages": first_optional_int(live_page_count, named.get("PageCount")),
            "words": words,
            "non_asian_words": non_asian_words,
            "asian_characters": asian_characters,
            "characters": characters,
            "characters_with_spaces": characters_with_spaces,
            "paragraphs": first_optional_int(getattr(document, "ParagraphCount", None), named.get("ParagraphCount")),
            "lines": to_optional_int(line_count),
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                try:
                    document.dispose()
                except Exception:
                    pass
        try:
            desktop.terminate()
        except Exception:
            pass


try:
    main()
except Exception as exc:
    print(
        json.dumps(
            {
                "error": f"{exc.__class__.__name__}: {exc}",
                "traceback": traceback.format_exc(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    sys.exit(2)
"""


def compute_libreoffice_document_statistics(raw_bytes: bytes, filename: str) -> dict[str, Any]:
    """使用 LibreOffice Writer 读取 Word/WPS 风格文档统计。"""
    suffix = _word_suffix(filename)
    soffice_path = find_libreoffice_soffice()
    python_path = find_libreoffice_python(soffice_path)
    version = get_libreoffice_version(soffice_path)
    timeout_seconds = _get_timeout_seconds()

    with TemporaryDirectory(prefix="lo-statistics-", ignore_cleanup_errors=True) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_path = temp_dir / f"source{suffix}"
        script_path = temp_dir / "statistics_probe.py"
        profile_dir = temp_dir / "profile"
        source_path.write_bytes(raw_bytes)
        script_path.write_text(_STATISTICS_SCRIPT, encoding="utf-8")
        profile_dir.mkdir(parents=True, exist_ok=True)

        port = _reserve_tcp_port()
        process = _start_listener(soffice_path, profile_dir, port)
        try:
            result = subprocess.run(
                [str(python_path), "-u", str(script_path), str(port), str(source_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                creationflags=_WINDOWS_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeError("LibreOffice 统计超时。") from exc
        finally:
            _stop_process(process)

    if result.returncode != 0:
        raise LibreOfficeError(_format_subprocess_error(result))

    payload = _parse_last_json_line(result.stdout)
    if not isinstance(payload, dict):
        raise LibreOfficeError("LibreOffice 统计没有返回有效 JSON。")
    if payload.get("error"):
        raise LibreOfficeError(str(payload["error"]))

    payload.update(
        {
            "source": "libreoffice",
            "engine": "libreoffice-writer",
            "engine_version": version,
            "license_status": None,
            "include_textboxes_footnotes_endnotes": True,
        }
    )
    return payload


def convert_word_to_docx(raw_bytes: bytes, filename: str) -> bytes:
    """将 legacy .doc 转成 DOCX；DOCX 原样返回。"""
    suffix = _word_suffix(filename)
    if suffix == ".docx":
        return raw_bytes
    if suffix != ".doc":
        raise LibreOfficeError(f"不支持转换 {suffix or '未知'} 文件。")

    soffice_path = find_libreoffice_soffice()
    timeout_seconds = _get_timeout_seconds()

    with TemporaryDirectory(prefix="lo-convert-", ignore_cleanup_errors=True) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        input_dir = temp_dir / "input"
        output_dir = temp_dir / "output"
        profile_dir = temp_dir / "profile"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        source_path = input_dir / "source.doc"
        source_path.write_bytes(raw_bytes)
        command = [
            str(soffice_path),
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--nolockcheck",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "docx",
            "--outdir",
            str(output_dir),
            str(source_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                creationflags=_WINDOWS_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeError("LibreOffice 转换 DOC 超时。") from exc

        if result.returncode != 0:
            raise LibreOfficeError(_format_subprocess_error(result))

        converted_path = output_dir / "source.docx"
        if not converted_path.exists():
            matches = list(output_dir.glob("*.docx"))
            if not matches:
                raise LibreOfficeError("LibreOffice 转换后没有生成 DOCX 文件。")
            converted_path = matches[0]
        return converted_path.read_bytes()


def build_converted_docx_filename(filename: str) -> str:
    path = Path(filename or "source.doc")
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    return f"{stem or 'source'}.docx"


def is_word_document(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in WORD_DOCUMENT_EXTENSIONS


@lru_cache
def find_libreoffice_soffice() -> Path:
    settings = get_settings()
    configured_path = (settings.libreoffice_soffice_path or "").strip()
    if configured_path:
        path = Path(configured_path)
        if path.is_file():
            return path
        raise LibreOfficeUnavailableError(f"LibreOffice 可执行文件不存在：{configured_path}")

    candidates = [
        shutil.which("soffice"),
        shutil.which("soffice.com"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/libreoffice",
        "/usr/local/bin/libreoffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise LibreOfficeUnavailableError("未找到 LibreOffice，请配置 LIBREOFFICE_SOFFICE_PATH。")


@lru_cache
def find_libreoffice_python(soffice_path: Path | None = None) -> Path:
    settings = get_settings()
    configured_path = (settings.libreoffice_python_path or "").strip()
    if configured_path:
        path = Path(configured_path)
        if path.is_file():
            return path
        raise LibreOfficeUnavailableError(f"LibreOffice Python 不存在：{configured_path}")

    soffice_path = soffice_path or find_libreoffice_soffice()
    program_dir = soffice_path.parent
    candidates = [
        program_dir / "python.exe",
        program_dir / "python",
        shutil.which("python3"),
        shutil.which("python"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise LibreOfficeUnavailableError("未找到可用于 UNO 的 Python。")


@lru_cache
def get_libreoffice_version(soffice_path: Path | None = None) -> str | None:
    soffice_path = soffice_path or find_libreoffice_soffice()
    try:
        result = subprocess.run(
            [str(soffice_path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=_WINDOWS_NO_WINDOW,
        )
    except Exception:
        return None
    version = (result.stdout or result.stderr or "").strip()
    return version or None


def _word_suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in WORD_DOCUMENT_EXTENSIONS:
        raise LibreOfficeError(f"不支持统计 {suffix or '未知'} 文件。")
    return suffix


def _get_timeout_seconds() -> float:
    try:
        return max(float(get_settings().libreoffice_timeout_seconds), 1.0)
    except (TypeError, ValueError):
        return 60.0


def _reserve_tcp_port() -> int:
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if 1024 <= port <= 65535:
            return port
    return random.randint(20000, 50000)


def _start_listener(soffice_path: Path, profile_dir: Path, port: int) -> subprocess.Popen:
    command = [
        str(soffice_path),
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext",
    ]
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_WINDOWS_NO_WINDOW,
    )


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _parse_last_json_line(output: str) -> Any:
    for line in reversed((output or "").splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            continue
    return None


def _format_subprocess_error(result: subprocess.CompletedProcess) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or f"退出码 {result.returncode}"
    return f"LibreOffice 调用失败：{detail}"
