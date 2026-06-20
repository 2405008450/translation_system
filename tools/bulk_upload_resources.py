"""Bulk upload scraped term/TM xlsx files to the translation workbench API."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

from datetime import datetime, timezone

import requests

FILENAME_RE = re.compile(r"^\d+_(.+)_(\d+)entries\.xlsx$", re.IGNORECASE)
MAX_NAME_LEN = 200

CANONICAL_LANG_CODES = {
    "zh-CN", "zh-TW", "zh-HK", "zh-MO", "en-US", "en-GB", "ja-JP", "ko-KR",
    "fr-FR", "de-DE", "es-ES", "pt-BR", "pt-PT", "it-IT", "ru-RU", "pl-PL",
    "nl-NL", "sv-SE", "da-DK", "fi-FI", "no-NO", "tr-TR", "uk-UA", "cs-CZ",
    "sk-SK", "ro-RO", "hu-HU", "el-GR", "bg-BG", "hr-HR", "sr-RS", "sl-SI",
    "lt-LT", "lv-LV", "et-EE", "ar-SA", "he-IL", "fa-IR", "ur-PK", "hi-IN",
    "bn-BD", "id-ID", "ms-MY", "th-TH", "vi-VN", "fil-PH", "my-MM", "km-KH",
    "lo-LA", "sw-KE",
}

LANG_PREFIX_FALLBACK = {
    "zh": "zh-CN",
    "en": "en-US",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
    "pt": "pt-BR",
    "it": "it-IT",
    "ru": "ru-RU",
    "pl": "pl-PL",
    "nl": "nl-NL",
    "sv": "sv-SE",
    "da": "da-DK",
    "fi": "fi-FI",
    "no": "no-NO",
    "nb": "no-NO",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "cs": "cs-CZ",
    "sk": "sk-SK",
    "ro": "ro-RO",
    "hu": "hu-HU",
    "el": "el-GR",
    "bg": "bg-BG",
    "hr": "hr-HR",
    "sr": "sr-RS",
    "sl": "sl-SI",
    "lt": "lt-LT",
    "lv": "lv-LV",
    "et": "et-EE",
    "ar": "ar-SA",
    "he": "he-IL",
    "fa": "fa-IR",
    "ur": "ur-PK",
    "hi": "hi-IN",
    "bn": "bn-BD",
    "id": "id-ID",
    "ms": "ms-MY",
    "th": "th-TH",
    "vi": "vi-VN",
    "fil": "fil-PH",
    "my": "my-MM",
    "km": "km-KH",
    "lo": "lo-LA",
    "sw": "sw-KE",
}

LANG_RULES: list[tuple[str, str, str]] = [
    ("中译英", "zh-CN", "en-US"),
    ("中英", "zh-CN", "en-US"),
    ("英译中", "en-US", "zh-CN"),
    ("英中", "en-US", "zh-CN"),
    ("韩中", "ko-KR", "zh-CN"),
    ("日译中", "ja-JP", "zh-CN"),
    ("中译韩", "zh-CN", "ko-KR"),
    ("中译日", "zh-CN", "ja-JP"),
    ("中译俄", "zh-CN", "ru-RU"),
    ("英译俄", "en-US", "ru-RU"),
    ("中译德", "zh-CN", "de-DE"),
    ("中译法", "zh-CN", "fr-FR"),
    ("中译阿拉伯", "zh-CN", "ar-SA"),
    ("中译巴葡", "zh-CN", "pt-BR"),
    ("中拉美西", "zh-CN", "es-ES"),
    ("中译西", "zh-CN", "es-ES"),
    ("中译波兰", "zh-CN", "pl-PL"),
    ("荷兰语", "zh-CN", "nl-NL"),
    ("英译乌克兰", "en-US", "uk-UA"),
    ("英译希伯来", "en-US", "he-IL"),
    ("英译挪威", "en-US", "no-NO"),
    ("英译阿语", "en-US", "ar-SA"),
]


def infer_language_pair(name: str) -> tuple[str, str]:
    for pattern, src, tgt in LANG_RULES:
        if pattern in name:
            return src, tgt
    return "zh-CN", "en-US"


def normalize_lang_code(code: str) -> str:
    cleaned = code.strip()
    if cleaned in CANONICAL_LANG_CODES:
        return cleaned
    lowered = cleaned.lower()
    for canonical in CANONICAL_LANG_CODES:
        if canonical.lower() == lowered:
            return canonical
    prefix = lowered.split("-", 1)[0]
    if prefix in LANG_PREFIX_FALLBACK:
        return LANG_PREFIX_FALLBACK[prefix]
    return "en-US"


def infer_term_language_pair(name: str) -> tuple[str, str]:
    src, tgt = infer_language_pair(name)
    return normalize_lang_code(src), normalize_lang_code(tgt)


def parse_xlsx_name(path: Path) -> tuple[str, int]:
    match = FILENAME_RE.match(path.name)
    if match:
        return match.group(1).strip(), int(match.group(2))
    stem = path.stem
    return stem[:MAX_NAME_LEN], 0


def truncate_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name).strip()
    if len(cleaned) <= MAX_NAME_LEN:
        return cleaned
    return cleaned[:MAX_NAME_LEN].rstrip()


def load_manifest(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("ok"):
                done.add(row.get("file") or row.get("path") or "")
    return done


def append_manifest(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError)):
        return True
    message = str(exc).lower()
    return (
        "connection aborted" in message
        or "remote end closed" in message
        or "connectionreset" in message
        or "强迫关闭" in message
    )


class UploadClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.token = ""
        self.login(username, password)
        self.tm_collections: dict[str, str] = {}
        self.term_collections: dict[str, str] = {}
        self.refresh_collection_maps()

    def reconnect(self) -> None:
        self.session = requests.Session()
        self.login(self.username, self.password)
        self.refresh_collection_maps()

    def login(self, username: str, password: str) -> None:
        res = self.session.post(
            f"{self.base_url}/api/auth/login",
            json={"username": username, "password": password},
            timeout=30,
        )
        if not res.ok:
            raise RuntimeError(f"登录失败: {res.status_code} {res.text[:300]}")
        self.token = res.json()["access_token"]
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def refresh_collection_maps(self) -> None:
        tm_res = self.session.get(f"{self.base_url}/api/translation-memory/collections", timeout=60)
        term_res = self.session.get(f"{self.base_url}/api/termbase/collections", timeout=60)
        if tm_res.ok:
            self.tm_collections = {item["name"]: item["id"] for item in tm_res.json()}
        if term_res.ok:
            self.term_collections = {item["name"]: item["id"] for item in term_res.json()}

    def get_or_create_tm_collection(self, name: str, src: str, tgt: str) -> str:
        if name in self.tm_collections:
            return self.tm_collections[name]
        res = self.session.post(
            f"{self.base_url}/api/translation-memory/collections",
            json={
                "name": name,
                "description": "从 YiCAT 批量导入",
                "source_language": src,
                "target_language": tgt,
            },
            timeout=60,
        )
        if res.status_code == 409:
            self.refresh_collection_maps()
            if name in self.tm_collections:
                return self.tm_collections[name]
        if not res.ok:
            raise RuntimeError(f"创建记忆库失败: {res.status_code} {res.text[:300]}")
        cid = res.json()["id"]
        self.tm_collections[name] = cid
        return cid

    def get_or_create_term_collection(self, name: str, src: str, tgt: str) -> str:
        if name in self.term_collections:
            return self.term_collections[name]
        res = self.session.post(
            f"{self.base_url}/api/termbase/collections",
            json={
                "name": name,
                "description": "从 YiCAT 批量导入",
                "source_language": src,
                "target_language": tgt,
            },
            timeout=60,
        )
        if res.status_code == 409:
            self.refresh_collection_maps()
            if name in self.term_collections:
                return self.term_collections[name]
        if not res.ok:
            raise RuntimeError(f"创建术语库失败: {res.status_code} {res.text[:300]}")
        cid = res.json()["id"]
        self.term_collections[name] = cid
        return cid

    def upload_tm(self, path: Path, collection_id: str, src: str, tgt: str) -> dict:
        size = path.stat().st_size
        # 大文件导入在服务端同步解析，HTTP 需等待整段处理完成
        timeout = max(600, int(size / 25000) + 600)
        if size >= 20 * 1024 * 1024:
            timeout = max(timeout, 7200)
        with path.open("rb") as fh:
            res = self.session.post(
                f"{self.base_url}/api/translation-memory/import",
                files={
                    "file": (
                        path.name,
                        fh,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                data={
                    "collection_id": collection_id,
                    "source_language": src,
                    "target_language": tgt,
                    "duplicate_policy": "overwrite",
                    "skip_header": "false",
                    "skip_duplicate_row_indexes": "[]",
                },
                timeout=timeout,
            )
        if not res.ok:
            raise RuntimeError(f"记忆库导入失败: {res.status_code} {res.text[:500]}")
        return res.json()

    def upload_term(self, path: Path, collection_id: str) -> dict:
        timeout = max(120, int(path.stat().st_size / 80000) + 60)
        with path.open("rb") as fh:
            res = self.session.post(
                f"{self.base_url}/api/termbase/import",
                files={
                    "file": (
                        path.name,
                        fh,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                data={"collection_id": collection_id},
                timeout=timeout,
            )
        if not res.ok:
            raise RuntimeError(f"术语库导入失败: {res.status_code} {res.text[:500]}")
        return res.json()


def sleep_for_file(path: Path, base_delay: float) -> None:
    size_mb = path.stat().st_size / (1024 * 1024)
    extra = 0.0
    if size_mb >= 50:
        extra = 45.0
    elif size_mb >= 20:
        extra = 25.0
    elif size_mb >= 10:
        extra = 15.0
    elif size_mb >= 1:
        extra = 5.0
    time.sleep(base_delay + extra)


def upload_folder(
    client: UploadClient,
    folder: Path,
    resource: str,
    manifest_path: Path,
    base_delay: float,
    retries: int,
    only_files: list[Path] | None = None,
    max_files: int = 0,
) -> tuple[int, int]:
    done = load_manifest(manifest_path)
    if only_files:
        files = sorted(only_files, key=lambda p: p.name)
    else:
        files = sorted(folder.glob("*.xlsx"), key=lambda p: p.name)
    ok_count = 0
    fail_count = 0
    processed_this_run = 0

    for index, path in enumerate(files, start=1):
        if max_files > 0 and processed_this_run >= max_files:
            print(f"[PAUSE] 已达本轮上限 max-files={max_files}，请稍后再继续。")
            break
        if path.name in done:
            print(f"[SKIP] {path.name}")
            continue

        display_name, _entries = parse_xlsx_name(path)
        collection_name = truncate_name(display_name)
        src_tm, tgt_tm = infer_language_pair(display_name)
        src_tm, tgt_tm = normalize_lang_code(src_tm), normalize_lang_code(tgt_tm)
        src_term, tgt_term = infer_term_language_pair(display_name)
        started = time.time()
        try:
            for attempt in range(1, retries + 1):
                try:
                    if resource == "tm":
                        cid = client.get_or_create_tm_collection(collection_name, src_tm, tgt_tm)
                        result = client.upload_tm(path, cid, src_tm, tgt_tm)
                    else:
                        cid = client.get_or_create_term_collection(collection_name, src_term, tgt_term)
                        result = client.upload_term(path, cid)
                    row = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "resource": resource,
                        "file": path.name,
                        "collection_name": collection_name,
                        "collection_id": cid,
                        "bytes": path.stat().st_size,
                        "imported_rows": result.get("imported_rows"),
                        "created_rows": result.get("created_rows"),
                        "updated_rows": result.get("updated_rows"),
                        "elapsed_s": round(time.time() - started, 1),
                        "ok": True,
                    }
                    append_manifest(manifest_path, row)
                    ok_count += 1
                    processed_this_run += 1
                    print(
                        f"[{index}/{len(files)}] OK {resource} {path.name} "
                        f"rows={row['imported_rows']} ({row['elapsed_s']}s)"
                    )
                    break
                except Exception as exc:
                    if attempt >= retries:
                        raise
                    wait_s = min(60, base_delay * attempt * 3)
                    if is_connection_error(exc):
                        print(f"[RETRY {attempt}] {path.name}: 连接中断，{wait_s:.0f}s 后重连…")
                        time.sleep(wait_s)
                        client.reconnect()
                    else:
                        print(f"[RETRY {attempt}] {path.name}: {exc}")
                        time.sleep(min(30, base_delay * attempt * 2))
        except Exception as exc:
            fail_count += 1
            processed_this_run += 1
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "resource": resource,
                "file": path.name,
                "collection_name": collection_name,
                "bytes": path.stat().st_size,
                "error": str(exc),
                "elapsed_s": round(time.time() - started, 1),
                "ok": False,
            }
            append_manifest(manifest_path, row)
            print(f"[{index}/{len(files)}] FAIL {resource} {path.name}: {exc}")

        sleep_for_file(path, base_delay)

    return ok_count, fail_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk upload term/TM xlsx to workbench server")
    parser.add_argument("--base-url", default="http://43.132.156.72:19013")
    parser.add_argument("--username", default="admin123")
    parser.add_argument("--password", default="admin123")
    parser.add_argument("--term-dir", default="术语库下载")
    parser.add_argument("--tm-dir", default="记忆库下载")
    parser.add_argument("--delay", type=float, default=8.0, help="基础间隔秒数（大文件会自动加长冷却）")
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--only", choices=["term", "tm", "both"], default="both")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="仅上传指定文件（可多次指定），相对项目根目录或绝对路径",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="本轮最多处理多少个待上传文件（0=不限制），用于分批减压",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    client = UploadClient(args.base_url, args.username, args.password)

    resolved_files: list[Path] = []
    for item in args.file:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved_files.append(candidate.resolve())

    total_ok = 0
    total_fail = 0

    if args.only in {"term", "both"} and not (resolved_files and args.only == "tm"):
        term_dir = (root / args.term_dir).resolve()
        manifest = term_dir / "upload_manifest_server.jsonl"
        term_only = [p for p in resolved_files if p.parent == term_dir] if resolved_files else None
        if resolved_files and not term_only:
            term_only = None
        if not resolved_files or term_only:
            print(f"==> 上传术语库: {term_dir}")
            ok, fail = upload_folder(
                client, term_dir, "term", manifest, args.delay, args.retries,
                only_files=term_only, max_files=args.max_files,
            )
            total_ok += ok
            total_fail += fail
            print(f"术语库完成: ok={ok} fail={fail}")

    if args.only in {"tm", "both"} and not (resolved_files and args.only == "term"):
        tm_dir = (root / args.tm_dir).resolve()
        manifest = tm_dir / "upload_manifest_server.jsonl"
        tm_only = [p for p in resolved_files if p.parent == tm_dir] if resolved_files else None
        if resolved_files and not tm_only and args.only == "tm":
            tm_only = resolved_files
        if not resolved_files or tm_only:
            print(f"==> 上传记忆库: {tm_dir}")
            ok, fail = upload_folder(
                client, tm_dir, "tm", manifest, args.delay, args.retries,
                only_files=tm_only, max_files=args.max_files,
            )
            total_ok += ok
            total_fail += fail
            print(f"记忆库完成: ok={ok} fail={fail}")

    print(f"全部完成: ok={total_ok} fail={total_fail}")


if __name__ == "__main__":
    main()
