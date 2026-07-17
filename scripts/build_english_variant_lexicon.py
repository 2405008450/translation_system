from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


LEXICON_COLUMNS = (
    "british",
    "american",
    "category",
    "form",
    "to_american_enabled",
    "to_british_enabled",
    "source_refs",
    "notes",
)


@dataclass
class LexiconRow:
    british: str
    american: str
    categories: set[str] = field(default_factory=set)
    forms: set[str] = field(default_factory=set)
    source_refs: set[str] = field(default_factory=set)
    to_american_enabled: bool = True
    to_british_enabled: bool = True
    notes: set[str] = field(default_factory=set)


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _form_name(header: str, fallback: str) -> str:
    normalized = header.replace("英式", "").replace("美式", "").strip()
    return normalized or fallback


def read_excel_rows(path: Path) -> list[LexiconRow]:
    merged: dict[tuple[str, str], LexiconRow] = {}
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for worksheet in workbook.worksheets:
            header_values = next(
                worksheet.iter_rows(min_row=1, max_row=1, values_only=True),
                (),
            )
            headers = [_clean(value) for value in header_values]
            column_pairs: list[tuple[int, int, str]] = []
            category = ""
            if (
                len(headers) >= 10
                and headers[0].startswith("英式")
                and headers[5].startswith("美式")
            ):
                category = "verb"
                column_pairs = [
                    (index, index + 5, _form_name(headers[index], f"form_{index + 1}"))
                    for index in range(5)
                ]
            elif (
                len(headers) >= 4
                and headers[0].startswith("英式")
                and headers[2].startswith("美式")
            ):
                category = "noun"
                column_pairs = [
                    (0, 2, _form_name(headers[0], "singular")),
                    (1, 3, _form_name(headers[1], "plural")),
                ]

            if not column_pairs:
                continue

            for row_number, values in enumerate(
                worksheet.iter_rows(min_row=2, values_only=True),
                start=2,
            ):
                for british_column, american_column, form_name in column_pairs:
                    if max(british_column, american_column) >= len(values):
                        continue
                    british = _clean(values[british_column])
                    american = _clean(values[american_column])
                    if not british or not american or british.casefold() == american.casefold():
                        continue

                    key = (british.casefold(), american.casefold())
                    item = merged.setdefault(key, LexiconRow(british=british, american=american))
                    item.categories.add(category)
                    item.forms.add(form_name)
                    item.source_refs.add(
                        f"{worksheet.title}!{get_column_letter(british_column + 1)}{row_number}:"
                        f"{get_column_letter(american_column + 1)}{row_number}"
                    )
    finally:
        workbook.close()

    rows = list(merged.values())
    _disable_ambiguous_directions(rows)
    return sorted(rows, key=lambda item: (item.british.casefold(), item.american.casefold()))


def _disable_ambiguous_directions(rows: list[LexiconRow]) -> None:
    british_targets: dict[str, set[str]] = defaultdict(set)
    american_targets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        british_targets[row.british.casefold()].add(row.american.casefold())
        american_targets[row.american.casefold()].add(row.british.casefold())

    for row in rows:
        if len(british_targets[row.british.casefold()]) > 1:
            row.to_american_enabled = False
            row.notes.add("英式源词存在多个美式目标，已禁用英转美")
        if len(american_targets[row.american.casefold()]) > 1:
            row.to_british_enabled = False
            row.notes.add("美式源词存在多个英式目标，已禁用美转英")

    # 已是目标方言的词必须能够被保护。若它同时还是本方向的源词，无法在不理解
    # 语义的情况下安全判断，因此禁用该源词方向，保证重复执行不会继续改写。
    changed = True
    while changed:
        changed = False
        american_targets_enabled = {
            row.american.casefold()
            for row in rows
            if row.to_american_enabled
        }
        british_targets_enabled = {
            row.british.casefold()
            for row in rows
            if row.to_british_enabled
        }
        for row in rows:
            if row.to_american_enabled and row.british.casefold() in american_targets_enabled:
                row.to_american_enabled = False
                row.notes.add("英式源词同时是英转美目标，为保证幂等已禁用英转美")
                changed = True
            if row.to_british_enabled and row.american.casefold() in british_targets_enabled:
                row.to_british_enabled = False
                row.notes.add("美式源词同时是美转英目标，为保证幂等已禁用美转英")
                changed = True


def validate_rows(rows: list[LexiconRow]) -> None:
    if not rows:
        raise ValueError("未从工作簿中识别到英美词汇")

    seen_pairs: set[tuple[str, str]] = set()
    to_american: dict[str, str] = {}
    to_british: dict[str, str] = {}
    for row in rows:
        british = row.british.casefold()
        american = row.american.casefold()
        if not british or not american or british == american:
            raise ValueError(f"存在无效词对：{row.british!r} / {row.american!r}")
        pair = (british, american)
        if pair in seen_pairs:
            raise ValueError(f"存在重复词对：{row.british!r} / {row.american!r}")
        seen_pairs.add(pair)
        if row.to_american_enabled:
            previous = to_american.setdefault(british, american)
            if previous != american:
                raise ValueError(f"启用的英转美映射冲突：{row.british!r}")
        if row.to_british_enabled:
            previous = to_british.setdefault(american, british)
            if previous != british:
                raise ValueError(f"启用的美转英映射冲突：{row.american!r}")

    if set(to_american).intersection(to_american.values()):
        raise ValueError("启用的英转美映射存在源词/目标词重叠，无法保证幂等")
    if set(to_british).intersection(to_british.values()):
        raise ValueError("启用的美转英映射存在源词/目标词重叠，无法保证幂等")


def write_csv(rows: list[LexiconRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=LEXICON_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "british": row.british,
                    "american": row.american,
                    "category": "|".join(sorted(row.categories)),
                    "form": "|".join(sorted(row.forms)),
                    "to_american_enabled": str(row.to_american_enabled).lower(),
                    "to_british_enabled": str(row.to_british_enabled).lower(),
                    "source_refs": "|".join(sorted(row.source_refs)),
                    "notes": "；".join(sorted(row.notes)),
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Excel 构建英美英语运行时词库")
    parser.add_argument("input", type=Path, help="英美词汇 Excel 文件")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("app/resources/english_variant_lexicon.csv"),
        help="输出 CSV 路径",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_excel_rows(args.input.expanduser().resolve())
    validate_rows(rows)
    output_path = args.output.expanduser().resolve()
    write_csv(rows, output_path)
    print(f"已生成 {output_path}，共 {len(rows)} 条词对")


if __name__ == "__main__":
    main()
