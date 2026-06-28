#!/usr/bin/env python3
"""Extract Kardia ECG samples from vector drawing commands in a PDF."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path


POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4
MM_PER_POINT = MM_PER_INCH / POINTS_PER_INCH
ECG_SPEED_MM_PER_SECOND = 25.0
ECG_GAIN_MM_PER_MV = 10.0
PAGE_NUMBER = 2
PAGE_FLIP_PATTERN = r"q\s+1\s+0\s+0\s+-1\s+0\s+792\s+cm\s+(.*?)\s+S\s+Q"
POINT_PATTERN = re.compile(
    r"([-+]?(?:\d+\.\d+|\d+|\.\d+))\s+"
    r"([-+]?(?:\d+\.\d+|\d+|\.\d+))\s+"
    r"([ml])\b",
)


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class StrokeBlock:
    position: int
    points: list[Point]


@dataclass(frozen=True)
class Row:
    row_number: int
    baseline_y: float
    points: list[Point]


def run_qpdf(args: list[str]) -> str:
    result = subprocess.run(
        ["qpdf", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def content_object_for_page(pdf_path: Path, page_number: int) -> str:
    show_pages = run_qpdf(["--show-pages", str(pdf_path)])
    page_header = re.compile(rf"^page\s+{page_number}:\s+", re.MULTILINE)
    match = page_header.search(show_pages)
    if match is None:
        raise ValueError(f"Could not find page {page_number} in qpdf page listing")

    page_text = show_pages[match.end() :]
    next_page = re.search(r"^page\s+\d+:\s+", page_text, re.MULTILINE)
    if next_page is not None:
        page_text = page_text[: next_page.start()]

    content_match = re.search(r"^\s+(\d+)\s+\d+\s+R\s*$", page_text, re.MULTILINE)
    if content_match is None:
        raise ValueError(f"Could not find content object for page {page_number}")
    return content_match.group(1)


def page_content_stream(pdf_path: Path, content_object: str) -> str:
    return run_qpdf(
        [
            f"--show-object={content_object}",
            "--filtered-stream-data",
            str(pdf_path),
        ],
    )


def parse_stroke_blocks(content: str) -> list[StrokeBlock]:
    blocks: list[StrokeBlock] = []
    for match in re.finditer(PAGE_FLIP_PATTERN, content, flags=re.S):
        points = [
            Point(float(x), float(y))
            for x, y, _operator in POINT_PATTERN.findall(match.group(1))
        ]
        if points:
            blocks.append(StrokeBlock(position=match.start(), points=points))
    return blocks


def dedupe_points(points: list[Point]) -> list[Point]:
    deduped: list[Point] = []
    for point in points:
        if not deduped or (
            abs(deduped[-1].x - point.x) > 1e-4 or abs(deduped[-1].y - point.y) > 1e-4
        ):
            deduped.append(point)
    return deduped


def median_dx(points: list[Point]) -> float:
    if len(points) < 2:
        return 0.0
    return statistics.median(
        points[index + 1].x - points[index].x for index in range(len(points) - 1)
    )


def is_waveform_block(block: StrokeBlock) -> bool:
    points = dedupe_points(block.points)
    if len(points) < 40:
        return False

    x_values = [point.x for point in points]
    if max(x_values) - min(x_values) < 10.0:
        return False

    dx = median_dx(points)
    return 0.23 < dx < 0.24


def extract_waveform_blocks(blocks: list[StrokeBlock]) -> list[StrokeBlock]:
    return [block for block in blocks if is_waveform_block(block)]


def extract_row_baselines(blocks: list[StrokeBlock]) -> list[float]:
    baselines: list[float] = []
    for block in blocks:
        points = block.points
        if len(points) < 8:
            continue

        for first, second in zip(points[0::2], points[1::2], strict=False):
            if (
                abs(first.y - second.y) < 1e-4
                and first.x < 25.0
                and second.x > 580.0
                and 100.0 < first.y < 750.0
            ):
                baselines.append(first.y)

    unique = sorted({round(value, 4) for value in baselines})
    row_like = [
        value
        for value in unique
        if not any(abs(value - border) < 1.0 for border in (79.00735, 759.3223))
    ]
    return row_like[:4]


def build_rows(
    waveform_blocks: list[StrokeBlock],
    baselines: list[float],
) -> list[Row]:
    row_points: dict[int, list[Point]] = {index: [] for index in range(len(baselines))}

    for block in waveform_blocks:
        points = dedupe_points(block.points)
        median_y = statistics.median(point.y for point in points)
        row_index = min(
            range(len(baselines)),
            key=lambda index: abs(baselines[index] - median_y),
        )
        row_points[row_index].extend(points)

    rows: list[Row] = []
    for index, baseline_y in enumerate(baselines):
        points = sorted(row_points[index], key=lambda point: point.x)
        points = dedupe_points(points)
        rows.append(Row(row_number=index + 1, baseline_y=baseline_y, points=points))
    return rows


def output_paths(pdf_path: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    stem = pdf_path.stem
    return (
        output_dir / f"{stem}_samples.csv",
        output_dir / f"{stem}_extraction_metadata.json",
        output_dir / f"{stem}_page2_content_stream.txt",
    )


def write_outputs(
    pdf_path: Path,
    output_dir: Path,
    content_object: str,
    content: str,
    rows: list[Row],
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path, metadata_path, stream_path = output_paths(pdf_path, output_dir)

    x_step_pt = statistics.median(
        row.points[index + 1].x - row.points[index].x
        for row in rows
        for index in range(len(row.points) - 1)
    )
    pt_per_second = ECG_SPEED_MM_PER_SECOND / MM_PER_POINT
    sample_rate_hz_from_pdf_spacing = pt_per_second / x_step_pt
    sample_rate_hz = float(round(sample_rate_hz_from_pdf_spacing))
    mv_per_point_y = MM_PER_POINT / ECG_GAIN_MM_PER_MV

    sample_index = 0
    with csv_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "sample_index",
                "time_seconds",
                "row",
                "row_sample_index",
                "row_elapsed_seconds",
                "pdf_x_pt",
                "pdf_y_pt",
                "row_baseline_y_pt",
                "millivolts",
            ],
        )
        writer.writeheader()
        for row in rows:
            for row_sample_index, point in enumerate(row.points):
                writer.writerow(
                    {
                        "sample_index": sample_index,
                        "time_seconds": f"{sample_index / sample_rate_hz:.9f}",
                        "row": row.row_number,
                        "row_sample_index": row_sample_index,
                        "row_elapsed_seconds": f"{row_sample_index / sample_rate_hz:.9f}",
                        "pdf_x_pt": f"{point.x:.5f}",
                        "pdf_y_pt": f"{point.y:.5f}",
                        "row_baseline_y_pt": f"{row.baseline_y:.5f}",
                        "millivolts": f"{(row.baseline_y - point.y) * mv_per_point_y:.9f}",
                    }
                )
                sample_index += 1

    stream_path.write_text(content)

    metadata = {
        "source_pdf": str(pdf_path),
        "page_number": PAGE_NUMBER,
        "content_object": content_object,
        "method": "Parsed vector m/l path coordinates from the PDF content stream via qpdf --filtered-stream-data.",
        "rasterized": False,
        "sample_count": sample_index,
        "duration_seconds": sample_index / sample_rate_hz,
        "sample_rate_hz": sample_rate_hz,
        "sample_rate_hz_from_pdf_spacing": sample_rate_hz_from_pdf_spacing,
        "sample_rate_note": "PDF coordinates are rounded; timing uses 9000 samples over the stated 30 second duration.",
        "x_step_pt": x_step_pt,
        "points_per_second": pt_per_second,
        "mm_per_point": MM_PER_POINT,
        "speed_mm_per_second": ECG_SPEED_MM_PER_SECOND,
        "gain_mm_per_mv": ECG_GAIN_MM_PER_MV,
        "mv_per_pdf_point_y": mv_per_point_y,
        "positive_mv_formula": "(row_baseline_y_pt - pdf_y_pt) * mv_per_pdf_point_y",
        "rows": [
            {
                "row": row.row_number,
                "sample_count": len(row.points),
                "baseline_y_pt": row.baseline_y,
                "x_start_pt": row.points[0].x,
                "x_end_pt": row.points[-1].x,
                "time_start_seconds": sum(len(prior.points) for prior in rows[:index])
                / sample_rate_hz,
                "time_end_seconds_exclusive": sum(
                    len(prior.points) for prior in rows[: index + 1]
                )
                / sample_rate_hz,
            }
            for index, row in enumerate(rows)
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return csv_path, metadata_path, stream_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract ECG samples from Kardia PDF vector path data.",
    )
    parser.add_argument("pdf", type=Path, help="Input PDF path")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("extracted"),
        help="Directory for CSV and metadata outputs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = args.pdf
    content_object = content_object_for_page(pdf_path, PAGE_NUMBER)
    content = page_content_stream(pdf_path, content_object)
    blocks = parse_stroke_blocks(content)
    waveform_blocks = extract_waveform_blocks(blocks)
    baselines = extract_row_baselines(blocks)
    if len(baselines) != 4:
        raise ValueError(f"Expected 4 ECG row baselines, found {len(baselines)}")

    rows = build_rows(waveform_blocks, baselines)
    if sum(len(row.points) for row in rows) == 0:
        raise ValueError("No waveform points found")

    csv_path, metadata_path, stream_path = write_outputs(
        pdf_path=pdf_path,
        output_dir=args.output_dir,
        content_object=content_object,
        content=content,
        rows=rows,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {metadata_path}")
    print(f"Wrote {stream_path}")


if __name__ == "__main__":
    main()
