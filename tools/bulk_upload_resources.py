"""Bulk upload scraped term/TM xlsx files to the translation workbench API."""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

FILENAME_RE = re.compile(r"^\d+_(.+)_(\d+)entries\.xlsx$", re.IGNORECASE)
MAX_NAME_LEN = 200

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


TERM_SUPPORTED = {"zh", "en", "ja", "ko", "fr", "de", "es", "pt", "it", "ru", "ar", "th", "vi"}
TM_SUPPORTED = {
    "zh-CN", "zh-TW", "zh-HK", "zh-MO", "en-US", "en-GB", "ja-JP", "ko-KR",
    "fr-FR", "de-DE", "es-ES", "pt-BR", "it-IT", "ru-RU", "ar-SA", "th-TH", "vi-VN",
}


def normalize_term_lang(code: str) -> str:
    code = code.strip()
    if code in TERM_SUPPORTED:
        return code
    if code.startswith("zh"):
        return "zh"
    if code.startswith("en"):
        return "en"
    return "en"


def normalize_tm_lang(code: str) -> str:
    if code in TM_SUPPORTED:
        return code
    if code.startswith("zh"):
        return "zh-CN"
    if code.startswith("en"):
        return "en-US"
    if code.startswith("ja"):
        return "ja-JP"
    if code.startswith("ko"):
        return "ko-KR"
    if code.startswith("ru"):
        return "ru-RU"
    if code.startswith("ar"):
        return "ar-SA"
    if code.startswith("de"):
        return "de-DE"
    if code.startswith("fr"):
        return "fr-FR"
    if code.startswith("es"):
        return "es-ES"
    if code.startswith("pt"):
        return "pt-BR"
    return "en-US"


def infer_term_language_pair(name: str) -> tuple[str, str]:
    src, tgt = infer_language_pair(name)
    if src == "zh-CN":
        src = "zh"
    if tgt == "en-US":
        tgt = "en"
    return normalize_term_lang(src), normalize_term_lang(tgt)


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


class UploadClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token = ""
        self.login(username, password)
        self.tm_collections: dict[str, str] = {}
        self.term_collections: dict[str, str] = {}
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
        timeout = max(180, int(path.stat().st_size / 50000) + 120)
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
        extra = 20.0
    elif size_mb >= 10:
        extra = 8.0
    elif size_mb >= 1:
        extra = 3.0
    time.sleep(base_delay + extra)


def upload_folder(
    client: UploadClient,
    folder: Path,
    resource: str,
    manifest_path: Path,
    base_delay: float,
    retries: int,
) -> tuple[int, int]:
    done = load_manifest(manifest_path)
    files = sorted(folder.glob("*.xlsx"), key=lambda p: p.name)
    ok_count = 0
    fail_count = 0

    for index, path in enumerate(files, start=1):
        if path.name in done:
            print(f"[SKIP] {path.name}")
            continue

        display_name, _entries = parse_xlsx_name(path)
        collection_name = truncate_name(display_name)
        src_tm, tgt_tm = infer_language_pair(display_name)
        src_tm, tgt_tm = normalize_tm_lang(src_tm), normalize_tm_lang(tgt_tm)
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
                        "ts": datetime.utcnow().isoformat(),
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
                    print(
                        f"[{index}/{len(files)}] OK {resource} {path.name} "
                        f"rows={row['imported_rows']} ({row['elapsed_s']}s)"
                    )
                    break
                except Exception as exc:
                    if attempt >= retries:
                        raise
                    print(f"[RETRY {attempt}] {path.name}: {exc}")
                    time.sleep(min(30, base_delay * attempt * 2))
        except Exception as exc:
            fail_count += 1
            row = {
                "ts": datetime.utcnow().isoformat(),
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
    parser.add_argument("--delay", type=float, default=2.5, help="基础间隔秒数（大文件会自动加长）")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--only", choices=["term", "tm", "both"], default="both")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    client = UploadClient(args.base_url, args.username, args.password)

    total_ok = 0
    total_fail = 0

    if args.only in {"term", "both"}:
        term_dir = (root / args.term_dir).resolve()
        manifest = term_dir / "upload_manifest_server.jsonl"
        print(f"==> 上传术语库: {term_dir}")
        ok, fail = upload_folder(client, term_dir, "term", manifest, args.delay, args.retries)
        total_ok += ok
        total_fail += fail
        print(f"术语库完成: ok={ok} fail={fail}")

    if args.only in {"tm", "both"}:
        tm_dir = (root / args.tm_dir).resolve()
        manifest = tm_dir / "upload_manifest_server.jsonl"
        print(f"==> 上传记忆库: {tm_dir}")
        ok, fail = upload_folder(client, tm_dir, "tm", manifest, args.delay, args.retries)
        total_ok += ok
        total_fail += fail
        print(f"记忆库完成: ok={ok} fail={fail}")

    print(f"全部完成: ok={total_ok} fail={total_fail}")


if __name__ == "__main__":
    main()
