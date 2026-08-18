"""East Money quote field enums — reverse-engineered from vendor.js (quote2019)."""
from __future__ import annotations

# enum_id -> f-field names (from vendor.js l.Yi mapping)
ENUM_FIDS: dict[int, tuple[str, ...]] = {
    21: ("f19", "f59", "f60", "f532"),
    22: ("f39", "f59", "f60", "f532"),
    23: ("f20", "f532"),
    24: ("f211",),
    25: ("f17", "f59", "f60", "f531"),
    26: ("f18", "f531"),
    27: ("f212",),
    28: ("f15", "f59", "f60", "f531"),
    29: ("f16", "f531"),
    30: ("f213",),
    31: ("f13", "f59", "f60", "f531"),
    32: ("f14", "f531"),
    33: ("f214",),
    34: ("f11", "f59", "f60", "f531"),
    35: ("f12", "f531"),
    36: ("f215",),
    37: ("f40", "f532"),
    38: ("f210",),
    39: ("f37", "f59", "f60", "f531"),
    40: ("f38", "f531"),
    41: ("f209",),
    42: ("f35", "f59", "f60", "f531"),
    43: ("f36", "f531"),
    44: ("f208",),
    45: ("f33", "f59", "f60", "f531"),
    46: ("f34", "f531"),
    47: ("f207",),
    48: ("f31", "f59", "f60", "f531"),
    49: ("f32", "f531"),
    50: ("f206",),
    # 卖十..卖六 (L2 extension via f21-f30)
    51: ("f530", "f59", "f60"),  # 卖十价 -> f21 via f530 indirection
    52: ("f530",),
    53: ("f530",),
    54: ("f530", "f59", "f60"),  # 卖九价 -> f23
    55: ("f530",),
    56: ("f530",),
    57: ("f530", "f59", "f60"),  # 卖八 -> f25
    58: ("f530",),
    59: ("f530",),
    60: ("f530", "f59", "f60"),  # 卖七 -> f27
    61: ("f530",),
    62: ("f530",),
    63: ("f530", "f59", "f60"),  # 卖六 -> f29
    64: ("f530",),
    65: ("f530",),
    # 买十..买六 via f1-f10
    71: ("f530", "f59", "f60"),  # 买十 -> f1
    72: ("f530",),
    73: ("f530",),
    74: ("f530", "f59", "f60"),  # 买九 -> f3
    75: ("f530",),
    76: ("f530",),
    77: ("f530", "f59", "f60"),  # 买八 -> f5
    78: ("f530",),
    79: ("f530",),
    80: ("f530", "f59", "f60"),  # 买七 -> f7
    81: ("f530",),
    82: ("f530",),
    83: ("f530", "f59", "f60"),  # 买六 -> f9
    84: ("f530",),
    85: ("f530",),
}

# Direct L2 price/volume slots when fltt=1 (vendor f530 value mappers)
L2_ASK_PRICE_FIELDS = ("f21", "f23", "f25", "f27", "f29")  # 卖10..卖6
L2_ASK_VOL_FIELDS = ("f22", "f24", "f26", "f28", "f30")
L2_BID_PRICE_FIELDS = ("f1", "f3", "f5", "f7", "f9")  # 买10..买6
L2_BID_VOL_FIELDS = ("f2", "f4", "f6", "f8", "f10")

# Standard 五档 (fltt=2 元, 或 fltt=1 分)
ASK_FIELD_PAIRS_L1 = (("f39", "f40"), ("f37", "f38"), ("f35", "f36"), ("f33", "f34"), ("f31", "f32"))
BID_FIELD_PAIRS_L1 = (("f19", "f20"), ("f17", "f18"), ("f15", "f16"), ("f13", "f14"), ("f11", "f12"))

# vendor.js 常用行情 enum 集合（含五档）
QUOTE_BASIC_ENUMS = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19, 20,
    21, 22, 23, 25, 26, 28, 29, 31, 32, 34, 35, 37, 39, 40, 42, 43, 45, 46, 48, 49,
)

# 十档扩展 enums（卖六..卖十、买六..买十）
QUOTE_L2_DEPTH_ENUMS = (
    51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65,
    71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85,
)


def build_fields_param(enum_ids: tuple[int, ...] | list[int]) -> str:
    """Build ``fields`` query like vendor.js ``_(enums)``."""
    seen: list[str] = []
    for eid in enum_ids:
        for fid in ENUM_FIDS.get(int(eid), ()):
            if fid not in seen:
                seen.append(fid)
    # always include core + L2 slots when probing depth
    for fid in (
        "f43", "f57", "f58", "f60", "f170", "f44", "f45", "f46", "f47", "f48",
        *L2_ASK_PRICE_FIELDS, *L2_ASK_VOL_FIELDS, *L2_BID_PRICE_FIELDS, *L2_BID_VOL_FIELDS,
        "f11", "f12", "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f20",
        "f31", "f32", "f33", "f34", "f35", "f36", "f37", "f38", "f39", "f40",
        "f206", "f207", "f208", "f209", "f210", "f211", "f212", "f213", "f214", "f215",
        "f221", "f222", "f530", "f531", "f532",
    ):
        if fid not in seen:
            seen.append(fid)
    return ",".join(seen)


FIELDS_TEN_DEPTH = build_fields_param([*QUOTE_BASIC_ENUMS, *QUOTE_L2_DEPTH_ENUMS])
