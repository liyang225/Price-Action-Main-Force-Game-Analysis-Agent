"""Audit pending records for validation retry feedback."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "records" / "pending"
OUT = Path(__file__).resolve().parents[1] / "records" / "_pending_audit.txt"


def main() -> None:
    lines: list[str] = []
    retry_details: list[tuple[str, str, str, str]] = []
    category = Counter()

    files = sorted(ROOT.glob("*.json"))
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        for stage_key in ("stage1_messages", "stage2_messages"):
            stage = "stage1" if stage_key == "stage1_messages" else "stage2"
            for m in data.get(stage_key) or []:
                c = str((m or {}).get("content") or "")
                if "校验未通过" not in c:
                    continue
                cat = "unknown"
                for marker in (
                    "category=a",
                    "category=b",
                    "category=c",
                    "category=d",
                    "category=e",
                    "category=f",
                ):
                    if marker in c:
                        cat = marker.split("=")[1]
                category[cat] += 1
                retry_details.append((p.name, stage, cat, c[:2000]))

    lines.append(f"TOTAL={len(files)}")
    lines.append(f"RETRY_FEEDBACK_COUNT={len(retry_details)}")
    lines.append(f"CATEGORIES={dict(category)}")
    lines.append("")
    for item in retry_details:
        lines.append("=" * 80)
        lines.append(f"FILE={item[0]} STAGE={item[1]} CAT={item[2]}")
        lines.append(item[3])

    lines.append("\n\nPARTIAL RECORDS")
    for p in files:
        data = json.loads(p.read_text(encoding="utf-8"))
        s1 = (data.get("stage1_response") or {}).get("status")
        s2 = (data.get("stage2_response") or {}).get("status")
        if s1 == "finished" and s2 == "finished":
            continue
        exc = data.get("exception")
        has_s1 = bool(data.get("stage1_diagnosis"))
        has_s2 = bool(data.get("stage2_decision"))
        lines.append(
            f"{p.name}: s1={s1} s2={s2} has_s1={has_s1} has_s2={has_s2} "
            f"exc={exc!r} partial={data.get('_partial_reason')!r}"
        )

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} retry={len(retry_details)}")


if __name__ == "__main__":
    main()
