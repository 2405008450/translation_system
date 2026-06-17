#!/usr/bin/env python3
"""翻译系统并发压力测试脚本。

模拟多个用户并发访问 API，统计吞吐量(RPS)、错误率与延迟分位数(p50/p90/p95/p99)，
用于验证多 worker 部署与连接池调优在并发下的表现。

依赖：
    pip install httpx

用法示例：
    # 只读场景（安全，默认）：10 个并发用户压 30 秒
    python scripts/load_test.py \
        --base-url http://127.0.0.1:19013 \
        --username admin --password 'your-password' \
        --concurrency 10 --duration 30

    # 打开某个文档反复读取句段（较重的读场景）
    python scripts/load_test.py --base-url ... --username ... --password ... \
        --scenario segments --file-record-id <FILE_RECORD_ID>

    # 保存译文写入场景（会修改数据：回写原译文，版本号会自增！需显式开启）
    python scripts/load_test.py --base-url ... --username ... --password ... \
        --scenario save --file-record-id <FILE_RECORD_ID> --allow-writes

    # 混合场景（读为主，少量写）
    python scripts/load_test.py --base-url ... --username ... --password ... \
        --scenario mixed --file-record-id <FILE_RECORD_ID> --allow-writes

提示：
    - 写入场景会让目标文档的句段版本号自增并产生修订记录，请只在测试数据上使用。
    - 想同时观察数据库连接数，可在服务器另开终端循环执行 pg_stat_activity 查询。
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖 httpx，请先执行: pip install httpx")


@dataclass
class Sample:
    label: str
    status: int
    latency_ms: float
    ok: bool


@dataclass
class Results:
    samples: list[Sample] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


async def _login(client: httpx.AsyncClient, base_url: str, username: str, password: str) -> str:
    resp = await client.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
    )
    if resp.status_code != 200:
        sys.exit(f"登录失败 ({resp.status_code}): {resp.text[:300]}")
    token = resp.json().get("access_token")
    if not token:
        sys.exit("登录响应中没有 access_token。")
    return token


async def _fetch_segments(
    client: httpx.AsyncClient, base_url: str, headers: dict, file_record_id: str, limit: int
) -> list[dict]:
    resp = await client.get(
        f"{base_url}/api/file-records/{file_record_id}/segments",
        headers=headers,
        params={"skip": 0, "limit": limit},
    )
    if resp.status_code != 200:
        sys.exit(f"获取句段失败 ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    segments = data.get("segments") or data.get("items") or []
    if not segments:
        sys.exit("目标文档没有可用句段，无法运行 segments/save 场景。")
    return segments


class VirtualUser:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict,
        args: argparse.Namespace,
        deadline: float,
        results: Results,
        save_targets: list[dict] | None,
    ) -> None:
        self.client = client
        self.base_url = base_url
        self.headers = headers
        self.args = args
        self.deadline = deadline
        self.results = results
        # 每个虚拟用户维护自己的 sentence_id -> 当前版本号，避免互相版本冲突过多。
        self.versions: dict[str, int] = {}
        self.save_targets = save_targets or []

    async def _timed(self, label: str, coro) -> None:
        start = time.perf_counter()
        ok = False
        status = 0
        try:
            resp = await coro
            status = resp.status_code
            ok = 200 <= status < 400
        except Exception:
            status = -1
            ok = False
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.results.add(Sample(label=label, status=status, latency_ms=latency_ms, ok=ok))

    async def _do_read_projects(self) -> None:
        await self._timed(
            "GET /api/projects",
            self.client.get(f"{self.base_url}/api/projects", headers=self.headers),
        )

    async def _do_read_file_records(self) -> None:
        await self._timed(
            "GET /api/file-records",
            self.client.get(f"{self.base_url}/api/file-records", headers=self.headers),
        )

    async def _do_read_segments(self) -> None:
        fid = self.args.file_record_id
        await self._timed(
            "GET /segments",
            self.client.get(
                f"{self.base_url}/api/file-records/{fid}/segments",
                headers=self.headers,
                params={"skip": 0, "limit": self.args.page_limit},
            ),
        )

    async def _do_save(self) -> None:
        fid = self.args.file_record_id
        target = random.choice(self.save_targets)
        sentence_id = target["sentence_id"]
        version = self.versions.get(sentence_id, target.get("version") or 1)
        body = {
            "sentence_id": sentence_id,
            # 回写原译文，不改变内容（但版本号会自增）。
            "target_text": target.get("target_text") or "",
            "base_version": version,
            "track_revision": False,
        }
        start = time.perf_counter()
        status = 0
        ok = False
        try:
            resp = await self.client.put(
                f"{self.base_url}/api/file-records/{fid}/segments/{sentence_id}",
                headers=self.headers,
                json=body,
            )
            status = resp.status_code
            if status == 200:
                payload = resp.json()
                conflicts = payload.get("conflicts") or []
                if conflicts:
                    # 版本冲突：用服务器返回的当前版本号刷新，本次记为非成功。
                    self.versions[sentence_id] = int(conflicts[0].get("current_version") or version)
                    ok = False
                else:
                    new_version = payload.get("version")
                    self.versions[sentence_id] = int(new_version) if new_version else version + 1
                    ok = True
            else:
                ok = False
        except Exception:
            status = -1
            ok = False
        latency_ms = (time.perf_counter() - start) * 1000.0
        self.results.add(Sample(label="PUT /segments/{id}", status=status, latency_ms=latency_ms, ok=ok))

    async def run(self) -> None:
        scenario = self.args.scenario
        while time.perf_counter() < self.deadline:
            if scenario == "read":
                if random.random() < 0.5:
                    await self._do_read_projects()
                else:
                    await self._do_read_file_records()
            elif scenario == "segments":
                await self._do_read_segments()
            elif scenario == "save":
                await self._do_save()
            elif scenario == "mixed":
                roll = random.random()
                if roll < 0.6:
                    await self._do_read_segments()
                elif roll < 0.8:
                    await self._do_read_projects()
                else:
                    await self._do_save()
            if self.args.think_time > 0:
                await asyncio.sleep(self.args.think_time)


def _print_report(results: Results, duration: float, concurrency: int) -> None:
    samples = results.samples
    total = len(samples)
    if total == 0:
        print("没有采集到任何请求样本。")
        return

    ok_count = sum(1 for s in samples if s.ok)
    err_count = total - ok_count
    rps = total / duration if duration > 0 else 0.0
    latencies = sorted(s.latency_ms for s in samples)

    print("\n" + "=" * 60)
    print("压测结果汇总")
    print("=" * 60)
    print(f"并发用户数      : {concurrency}")
    print(f"实际运行时长    : {duration:.1f} s")
    print(f"总请求数        : {total}")
    print(f"成功 / 失败     : {ok_count} / {err_count}")
    print(f"错误率          : {err_count / total * 100:.2f}%")
    print(f"吞吐量 (RPS)    : {rps:.1f} req/s")
    print("-" * 60)
    print("延迟 (ms):")
    print(f"  平均  : {statistics.fmean(latencies):.1f}")
    print(f"  p50   : {_percentile(latencies, 50):.1f}")
    print(f"  p90   : {_percentile(latencies, 90):.1f}")
    print(f"  p95   : {_percentile(latencies, 95):.1f}")
    print(f"  p99   : {_percentile(latencies, 99):.1f}")
    print(f"  max   : {latencies[-1]:.1f}")
    print("-" * 60)

    status_counter = Counter(s.status for s in samples)
    print("HTTP 状态码分布:")
    for status, count in sorted(status_counter.items()):
        label = "连接/超时错误" if status == -1 else str(status)
        print(f"  {label:>14} : {count}")
    print("-" * 60)

    label_groups: dict[str, list[float]] = {}
    label_errs: Counter = Counter()
    for s in samples:
        label_groups.setdefault(s.label, []).append(s.latency_ms)
        if not s.ok:
            label_errs[s.label] += 1
    print("按接口拆分:")
    for label, lat in label_groups.items():
        slat = sorted(lat)
        print(
            f"  {label:<22} n={len(lat):<6} "
            f"p95={_percentile(slat, 95):7.1f}ms  errs={label_errs[label]}"
        )
    print("=" * 60)


async def _amain(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency * 2)
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(limits=limits, timeout=timeout, verify=not args.insecure) as client:
        token = await _login(client, base_url, args.username, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        if args.scenario in {"segments", "save", "mixed"} and not args.file_record_id:
            sys.exit(f"场景 {args.scenario} 需要 --file-record-id 参数。")
        if args.scenario in {"save", "mixed"} and not args.allow_writes:
            sys.exit("save/mixed 场景会写入数据，请确认后加 --allow-writes 显式开启。")

        save_targets: list[dict] | None = None
        if args.scenario in {"segments", "save", "mixed"}:
            segments = await _fetch_segments(client, base_url, headers, args.file_record_id, args.page_limit)
            save_targets = [
                {
                    "sentence_id": seg.get("sentence_id"),
                    "target_text": seg.get("target_text") or "",
                    "version": seg.get("version") or 1,
                }
                for seg in segments
                if seg.get("sentence_id")
            ]
            print(f"已加载目标文档句段 {len(save_targets)} 条用于读/写场景。")

        results = Results()
        print(
            f"开始压测：scenario={args.scenario} concurrency={args.concurrency} "
            f"duration={args.duration}s base_url={base_url}"
        )
        deadline = time.perf_counter() + args.duration
        started = time.perf_counter()
        users = [
            VirtualUser(client, base_url, headers, args, deadline, results, save_targets)
            for _ in range(args.concurrency)
        ]
        await asyncio.gather(*(u.run() for u in users))
        elapsed = time.perf_counter() - started

    _print_report(results, elapsed, args.concurrency)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="翻译系统并发压力测试")
    parser.add_argument("--base-url", required=True, help="服务地址，例如 http://127.0.0.1:19013")
    parser.add_argument("--username", required=True, help="登录用户名")
    parser.add_argument("--password", required=True, help="登录密码")
    parser.add_argument("--concurrency", type=int, default=10, help="并发虚拟用户数（默认 10）")
    parser.add_argument("--duration", type=float, default=30.0, help="压测时长秒数（默认 30）")
    parser.add_argument(
        "--scenario",
        choices=["read", "segments", "save", "mixed"],
        default="read",
        help="测试场景：read=只读列表(安全默认)；segments=反复读某文档句段；save=保存译文写入；mixed=读为主少量写",
    )
    parser.add_argument("--file-record-id", default=None, help="segments/save/mixed 场景的目标文档 ID")
    parser.add_argument("--allow-writes", action="store_true", help="允许写入（save/mixed 场景必需，会修改数据）")
    parser.add_argument("--think-time", type=float, default=0.2, help="每个用户两次请求间的间隔秒数（默认 0.2）")
    parser.add_argument("--page-limit", type=int, default=100, help="读句段分页大小（默认 100）")
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时秒数（默认 30）")
    parser.add_argument("--insecure", action="store_true", help="跳过 TLS 证书校验")
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\n已中断。")


if __name__ == "__main__":
    main()
