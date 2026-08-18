import re
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "prompt_engineering" / "极速上涨交易策略.txt"
text = p.read_text(encoding="utf-8")
text = re.sub(r"[\ufffd\ufeff]+", "", text)
lines = text.splitlines()
out = []
for ln in lines:
    if "止损类型3" in ln and ln.strip() != "止损类型3：技术位止损":
        out.append("止损类型3：技术位止损")
    else:
        out.append(ln)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("ok", p)
