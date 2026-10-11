"""Label curation for CORD-v2 receipts.

Turns the raw ground truth into a simplified schema and counts every change,
so the README can report exactly what curation did.
Target schema: {"menu": [{"nm": str, "cnt": int|None, "price": int|None}], "total": int}
A receipt is excluded (never partially kept) if its total or any item name is unusable.
"""
import json
import re
from collections import Counter

KEEP_ITEM_KEYS = {"nm", "cnt", "price"}


def norm_price(s):
    """Receipt price string -> int rupiah, or None if empty/unparseable/ambiguous."""
    if s is None or not isinstance(s, (str, int, float)):
        return None
    s = re.sub(r"[^\d.,]", "", str(s)).strip(".,")
    if not s:
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        return int(round(float(s.replace(thou, "").replace(dec, "."))))
    if has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = s.split(sep)
        if all(len(p) == 3 for p in parts[1:]):  # 75,000 / 75.000 / 1.000.000
            return int("".join(parts))
        if len(parts) >= 3 and len(parts[-1]) == 2 and all(len(p) == 3 for p in parts[1:-1]):
            # '39,200,00' / '22.000.00': thousands separators then a typo'd decimal
            return int(round(float("".join(parts[:-1]) + "." + parts[-1])))
        if len(parts) == 2:
            return int(round(float(parts[0] + "." + parts[1])))
        return None
    return int(s)


def norm_cnt(c):
    """Count string -> int, or None. Accepts '1', 'x3', '2X', '1 x', '1.00', '1Prs', '*2', '*4 Pcs'.
    Ambiguous values such as '1,000', '3.000x' or '0.264X' return None on purpose."""
    if c is None:
        return None
    m = re.fullmatch(
        r"\s*[*xX]?\s*(\d+)(?:[.,]0{1,2})?\.?\s*(?:x|prs|pcs|pc)?\s*",
        str(c),
        flags=re.IGNORECASE,
    )
    return int(m.group(1)) if m else None


def norm_name(nm):
    """Item name -> clean str, or None.
    A list is a name split into tokens with stray annotations ('-', '1'); drop tokens with
    no letters and accept the result only if exactly one token remains (else ambiguous)."""
    if isinstance(nm, list):
        toks = [t for t in nm if isinstance(t, str) and re.search(r"[^\W\d_]", t)]
        nm = toks[0] if len(toks) == 1 else None
    if not isinstance(nm, str):
        return None
    nm = " ".join(nm.split())
    return nm or None


def simplify(gt_parse, stats):
    """Return the simplified target, or None if the receipt is unusable (reason logged in stats)."""
    t = gt_parse.get("total")
    if not isinstance(t, dict) or "total_price" not in t:
        stats["excluded_no_total_price"] += 1
        return None
    total = norm_price(t["total_price"])
    if total is None:
        stats["excluded_unparseable_total"] += 1
        return None

    m = gt_parse.get("menu", [])
    raw_items = [m] if isinstance(m, dict) else list(m)

    local = Counter()  # merged into stats only if the receipt is kept
    if isinstance(m, dict):
        local["menu_dict_to_list"] += 1
    items = []
    for it in raw_items:
        local["items_seen"] += 1
        nm = norm_name(it.get("nm"))
        if nm is None:
            stats["excluded_unnamed_item"] += 1
            return None
        for k in it:
            if k not in KEEP_ITEM_KEYS:
                local[f"dropped_key:{k}"] += 1
        price = norm_price(it.get("price"))
        if price is None:
            local["price_null"] += 1
        cnt = norm_cnt(it.get("cnt"))
        if cnt is None:
            local["cnt_missing" if it.get("cnt") is None else "cnt_unparseable"] += 1
        items.append({"nm": nm, "cnt": cnt, "price": price})

    if not items:
        stats["excluded_no_items"] += 1
        return None
    stats.update(local)
    return {"menu": items, "total": total}


def curate_split(split_ds):
    """Returns (kept_indices, targets, stats) for one split. Reads labels only, not images."""
    stats = Counter()
    kept, targets = [], []
    for i, g in enumerate(split_ds["ground_truth"]):
        stats["receipts_in"] += 1
        tgt = simplify(json.loads(g)["gt_parse"], stats)
        if tgt is not None:
            kept.append(i)
            targets.append(tgt)
    stats["receipts_kept"] = len(kept)
    return kept, targets, stats


if __name__ == "__main__":
    assert norm_price("75,000") == 75000 and norm_price("40,000.") == 40000
    assert norm_price("Rp. 99.999") == 99999 and norm_price("9.999") == 9999
    assert norm_price("0") == 0 and norm_price("") is None
    assert norm_price("22.000.00") == 22000 and norm_price("39,200,00") == 39200
    assert norm_price(["55,834", "55,800"]) is None
    assert norm_cnt("1 x") == 1 and norm_cnt("x3") == 3 and norm_cnt("2X") == 2
    assert norm_cnt("1.00") == 1 and norm_cnt("1Prs") == 1 and norm_cnt("*4 Pcs") == 4
    assert norm_cnt("1,000") is None and norm_cnt("0.264X") is None and norm_cnt("3.000x") is None
    assert norm_name(["-", "Gyro Platter Large"]) == "Gyro Platter Large"
    assert norm_name(["1", "MIE UP BSR C"]) == "MIE UP BSR C"
    assert norm_name(["TAKEOUT(1):", "Viet Latte"]) is None
    assert norm_name(None) is None and norm_name("A  B") == "A B"
    s = Counter()
    ex = {
        "menu": {"nm": "A  B", "cnt": "2 x", "price": "10.000", "sub": {"nm": "hot"}},
        "total": {"total_price": "20,000", "cashprice": "50,000"},
    }
    assert simplify(ex, s) == {"menu": [{"nm": "A B", "cnt": 2, "price": 10000}], "total": 20000}
    assert simplify({"menu": [], "total": {"cashprice": "1"}}, s) is None
    two = {"menu": [{"nm": "A", "price": "1,000"}, {"cnt": "1", "price": "2,000"}],
           "total": {"total_price": "3,000"}}
    s2 = Counter()
    assert simplify(two, s2) is None and s2["excluded_unnamed_item"] == 1
    print("self-test ok")