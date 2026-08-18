"""Audit stage-2 reasoning claims against K-line tables in pending records."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PENDING = Path(__file__).resolve().parents[1] / "records" / "pending"


def parse_kline_table(user: str) -> dict[int, dict]:
    start = user.find("## K线数据")
    end = user.find("## K线几何特征")
    if start < 0 or end < 0:
        return {}
    rows: dict[int, dict] = {}
    for line in user[start:end].splitlines():
        if re.match(r"^\d+\s+\|", line):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 6:
                seq = int(parts[0])
                rows[seq] = {
                    "time": parts[1],
                    "open": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "close": float(parts[5]),
                }
    return rows


def parse_feature_table(user: str) -> dict[int, dict]:
    start = user.find("## K线几何特征")
    if start < 0:
        return {}
    rows: dict[int, dict] = {}
    for line in user[start:].splitlines():
        if re.match(r"^\d+\s+\|", line):
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 15:
                rows[int(parts[0])] = {"type": parts[1], "breakout": parts[14]}
    return rows


def find_price_in_text(text: str, val: float, tol: float = 0.15) -> str | None:
    for prec in range(5):
        s = f"{val:.{prec}f}".rstrip("0").rstrip(".")
        if s and s in text:
            return s
    for m in re.finditer(r"41\d{2}(?:\.\d+)?", text):
        try:
            x = float(m.group())
        except ValueError:
            continue
        if abs(x - val) <= tol:
            return m.group()
    return None


def audit_record(path: Path) -> str | None:
    rec = json.loads(path.read_text(encoding="utf-8"))
    s2 = rec.get("stage2_messages") or []
    user = next((m.get("content", "") for m in s2 if m.get("role") == "user"), "")
    reasoning = (rec.get("stage2_response") or {}).get("reasoning_content") or ""
    if not user or not reasoning:
        return None

    klines = parse_kline_table(user)
    feats = parse_feature_table(user)
    s1 = rec.get("stage1_diagnosis") or {}
    bar_analysis = s1.get("bar_analysis") or {}
    meta = rec.get("meta") or {}

    lines: list[str] = []
    lines.append(f"FILE: {path.name}")
    lines.append(
        f"symbol={meta.get('symbol')} tf={meta.get('timeframe')} "
        f"bars={len(klines)} reasoning_len={len(reasoning)}"
    )

    s1_bt = bar_analysis.get("bar_type")
    feat_k1 = feats.get(1, {}).get("type")
    lines.append(
        f"[棒型·阶段一] bar_analysis.bar_type={s1_bt} | 几何表K1.type={feat_k1}"
    )

    price_patterns: list[tuple[str, str | None]] = [
        (r"K(\d{1,3})\s*(?:的)?\s*(?:高点|最高价|high)\s*[=约为是]?\s*(41\d{2}(?:\.\d+)?)", "high"),
        (r"K(\d{1,3})\s*(?:的)?\s*(?:低点|最低价|low)\s*[=约为是]?\s*(41\d{2}(?:\.\d+)?)", "low"),
        (r"K(\d{1,3})\s*(?:的)?\s*(?:收盘|收盘价|close)\s*[=约为是]?\s*(41\d{2}(?:\.\d+)?)", "close"),
        (r"K(\d{1,3})\s*(?:的)?\s*(?:开盘|开盘价|open)\s*[=约为是]?\s*(41\d{2}(?:\.\d+)?)", "open"),
        (r"K(\d{1,3})\s+high\s*[=:]?\s*(41\d{2}(?:\.\d+)?)", "high"),
        (r"K(\d{1,3})\s+low\s*[=:]?\s*(41\d{2}(?:\.\d+)?)", "low"),
        (r"K(\d{1,3})\s+close\s*[=:]?\s*(41\d{2}(?:\.\d+)?)", "close"),
        (r"(高点|低点|收盘)\s*(41\d{2}(?:\.\d+)?)\s*[\(（]?\s*K(\d{1,3})", None),
        (r"K(\d{1,3})\s*(?:高点|低点|收盘|high|low|close)\s*(41\d{2}(?:\.\d+)?)", None),
    ]

    seen: set[tuple] = set()
    price_claims: list[tuple] = []
    for pat, field in price_patterns:
        for m in re.finditer(pat, reasoning, re.I):
            if field is None and m.lastindex and m.lastindex >= 3:
                label, price_s, seq_s = m.group(1), m.group(2), m.group(3)
                field2 = {"高点": "high", "最低价": "low", "低点": "low", "收盘": "close"}.get(label, "close")
                seq, price = int(seq_s), float(price_s)
            elif field is None:
                seq, price = int(m.group(1)), float(m.group(2))
                ctx = m.group(0)
                if "高点" in ctx or "high" in ctx.lower():
                    field2 = "high"
                elif "低点" in ctx or "low" in ctx.lower():
                    field2 = "low"
                elif "收盘" in ctx or "close" in ctx.lower():
                    field2 = "close"
                else:
                    continue
            else:
                seq, price = int(m.group(1)), float(m.group(2))
                field2 = field
            key = (seq, field2, round(price, 3))
            if key in seen:
                continue
            seen.add(key)
            actual = klines.get(seq, {}).get(field2)
            if actual is None:
                status = "K不存在"
            else:
                diff = abs(price - actual)
                status = "OK" if diff <= 0.15 else f"偏差{diff:.3f}"
            price_claims.append((seq, field2, price, actual, status, m.group(0)[:72]))

    lines.append(f"[价格·显式表述] {len(price_claims)} 条:")
    ok_n = sum(1 for c in price_claims if c[4] == "OK")
    bad = [c for c in price_claims if c[4] != "OK"]
    for c in price_claims[:15]:
        seq, fld, p, a, status, ctx = c
        lines.append(f"  K{seq} {fld}: 思考={p} 表={a} -> {status} | {ctx}")
    if len(price_claims) > 15:
        lines.append(f"  ... 另有 {len(price_claims) - 15} 条")
    lines.append(f"  小结: {ok_n}/{len(price_claims)} 显式价格表述与表一致")

    bt_claims: list[tuple] = []
    for m in re.finditer(
        r"K(\d{1,3})\s*(?:为|是|呈|属于)?\s*"
        r"(trend_bull|trend_bear|doji|inside|outside_bull|outside_bear|flat|other|阳线|阴线|外包|内包|十字星)",
        reasoning,
    ):
        seq = int(m.group(1))
        claimed = m.group(2)
        actual = feats.get(seq, {}).get("type", "?")
        zh_map = {
            "阳线": "trend_bull",
            "阴线": "trend_bear",
            "十字星": "doji",
            "内包": "inside",
            "外包": "outside_bull",
        }
        norm = zh_map.get(claimed, claimed)
        ok = norm == actual
        bt_claims.append((seq, claimed, actual, "OK" if ok else "不符", m.group(0)[:55]))

    for m in re.finditer(r"K(\d{1,3})[^。\n]{0,20}(?:up|down)\s*突破", reasoning, re.I):
        seq = int(m.group(1))
        actual = feats.get(seq, {}).get("breakout", "?")
        word = "up" if "up" in m.group(0).lower() else "down"
        bt_claims.append(
            (seq, f"{word}突破", actual, "OK" if actual == word else f"表={actual}", m.group(0)[:55])
        )

    lines.append(f"[棒型/突破·显式表述] {len(bt_claims)} 条:")
    for c in bt_claims[:12]:
        lines.append(f"  K{c[0]} 思考={c[1]} 几何表={c[2]} -> {c[3]} | {c[4]}")

    k_refs = sorted({int(m.group(1)) for m in re.finditer(r"K(\d{1,3})", reasoning)})
    lines.append(f"[抽查·提到的K序号] {k_refs[:25]}")
    for seq in k_refs[:10]:
        if seq not in klines:
            lines.append(f"  K{seq}: 不在表中（可能越界）")
            continue
        k = klines[seq]
        hits = {f: find_price_in_text(reasoning, k[f]) for f in ("high", "low", "close")}
        f = feats.get(seq, {})
        lines.append(
            f"  K{seq} 表 type={f.get('type')} breakout={f.get('breakout')} "
            f"H/L/C={k['high']}/{k['low']}/{k['close']} | "
            f"思考提及: high={hits['high'] or '—'} low={hits['low'] or '—'} close={hits['close'] or '—'}"
        )

    dec = (rec.get("stage2_decision") or {}).get("decision") or {}
    ot = dec.get("order_type")
    if ot and ot != "不下单":
        lines.append(f"[决策三价] order_type={ot}")
        for fld, label in (
            ("entry_price", "entry"),
            ("stop_loss_price", "stop"),
            ("take_profit_price", "tp"),
        ):
            v = dec.get(fld)
            if v is not None:
                found = find_price_in_text(reasoning, float(v))
                lines.append(f"  {label}={v} 思考中: {found or '未提及'}")

    if bad:
        lines.append("[明显偏差明细]")
        for c in bad[:8]:
            seq, fld, p, a, status, ctx = c
            lines.append(f"  K{seq} {fld}: 思考={p} 表={a} ({status})")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    indices = sys.argv[1:] if len(sys.argv) > 1 else ["-1", "-5", "middle", "0"]
    candidates = sorted(PENDING.glob("*.json"))
    valid: list[Path] = []
    for p in candidates:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if (rec.get("stage2_response") or {}).get("reasoning_content"):
                user = next(
                    (m.get("content", "") for m in (rec.get("stage2_messages") or []) if m.get("role") == "user"),
                    "",
                )
                if user and "## K线数据" in user:
                    valid.append(p)
        except (json.JSONDecodeError, OSError):
            continue

    picks: list[Path] = []
    for idx in indices:
        if idx == "middle":
            picks.append(valid[len(valid) // 2])
        else:
            picks.append(valid[int(idx)])

    seen: set[Path] = set()
    for p in picks:
        if p not in seen:
            seen.add(p)
            r = audit_record(p)
            if r:
                print(r)


if __name__ == "__main__":
    main()
