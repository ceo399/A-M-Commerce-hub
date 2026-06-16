"""???????????????????????? CSV/Excel ????

_????????.xlsx?????????????????
catalog_service.sync_catalog ?????????????
(csv) ? openpyxl ???pandas????

 ParseResult:
  rows     : sync_catalog ??????????????????
  errors   : ?????????????????
  warnings : ?????????????
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

# ???????xlsx???????????????
DATA_SHEET = "?????"

# ????????????????
ACTIVE_WORDS = {"???", "???", "???", "????", "active"}
DISCONTINUED_WORDS = {"????", "????", "??", "??", "??", "discontinued"}

# ??? ? ???????????spec.* ???????????
HEADER_MAP = {
    "SKU": "sku",
    "???": "name",
    "JAN???": "jan",
    "?????": "manufacturer",
    "?????": "brand",
    "????": "_supply",
    "???": "_stock",
    "????": "spec.category",
    "??????": "list_price",
    "????": "cost_price",
    "?": "spec.color",
    "???/??": "spec.size",
    "??": "spec.weight",
    "??": "spec.material",
    "????/??": "spec.compatibility",
    "???????/URL": "_images",
}


@dataclass
class ParseResult:
    rows: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    total_rows: int = 0

    def summary(self) -> dict:
        return {"total": self.total_rows, "ok": len(self.rows),
                "errors": len(self.errors), "warnings": len(self.warnings)}


# --------------------------------------------------------------------------
# ????
# --------------------------------------------------------------------------
def parse_catalog(path: str | Path, sheet: str | None = None) -> ParseResult:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        header, records = _read_xlsx(path, sheet)
    elif suffix in (".csv", ".tsv", ".txt"):
        header, records = _read_csv(path, delimiter="\t" if suffix == ".tsv" else ",")
    else:
        raise ValueError(f"????????????: {suffix}?CSV ??? xlsx?")
    return _normalize(header, records)


# --------------------------------------------------------------------------
# ?????????????
# --------------------------------------------------------------------------
def _read_csv(path: Path, delimiter: str = ",") -> tuple[list[str], list[list[str]]]:
    # utf-8-sig ? BOM ???????? cp932(Shift_JIS) ????
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    reader = [r for r in reader if any((c or "").strip() for c in r)]  # ????
    if not reader:
        return [], []
    return [c.strip() for c in reader[0]], reader[1:]


def _read_xlsx(path: Path, sheet: str | None) -> tuple[list[str], list[list[str]]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    name = sheet or (DATA_SHEET if DATA_SHEET in wb.sheetnames else wb.sheetnames[0])
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    rows = [r for r in rows if any(c not in (None, "") for c in r)]  # ????
    if not rows:
        return [], []
    header = [(str(c).strip() if c is not None else "") for c in rows[0]]
    body = [[("" if c is None else c) for c in r] for r in rows[1:]]
    return header, body


# --------------------------------------------------------------------------
# ??????
# --------------------------------------------------------------------------
def _normalize(header: list[str], records: list[list[str]]) -> ParseResult:
    result = ParseResult(total_rows=len(records))
    if not header:
        result.errors.append({"row": 0, "message": "????????????"})
        return result

    # ??????????
    feature_idx = [i for i, h in enumerate(header) if h.startswith("??")]
    known = set(HEADER_MAP) | {header[i] for i in feature_idx}
    seen_sku: set[str] = set()

    for n, record in enumerate(records, start=2):  # 2??=???????
        def cell(col_name: str) -> str:
            try:
                i = header.index(col_name)
            except ValueError:
                return ""
            return _s(record[i]) if i < len(record) else ""

        sku = cell("SKU")
        if not sku:
            result.errors.append({"row": n, "message": "SKU????????"})
            continue
        if sku in seen_sku:
            result.warnings.append({"row": n, "message": f"SKU?????????: {sku}"})
            continue
        seen_sku.add(sku)

        name = cell("???")
        if not name:
            result.errors.append({"row": n, "message": f"???????????: {sku}"})
            continue

        # ???? ? ????
        supply = cell("????")
        stock = _to_int(cell("???"))
        if not supply:
            result.warnings.append({"row": n, "message": f"????????????????: {sku}"})
            discontinued = False
        elif supply in ACTIVE_WORDS:
            discontinued = False
        elif supply in DISCONTINUED_WORDS:
            discontinued = True
        else:
            result.warnings.append({"row": n, "message": f"?????{supply}????????????????: {sku}"})
            discontinued = False
        # ??????????????????????0???????????
        in_stock = not (discontinued and (stock or 0) <= 0)

        # ??????
        spec: dict = {}
        for h, target in HEADER_MAP.items():
            if target.startswith("spec."):
                v = cell(h)
                if v:
                    spec[target.split(".", 1)[1]] = v
        features = [_s(record[i]) for i in feature_idx if i < len(record) and _s(record[i])]
        if features:
            spec["features"] = features
        images_raw = cell("???????/URL")
        if images_raw:
            spec["images"] = [x.strip() for x in images_raw.replace("?", ";").split(";") if x.strip()]
        # ?????????????
        for i, h in enumerate(header):
            if h and h not in known and i < len(record):
                v = _s(record[i])
                if v:
                    spec[h] = v

        row = {
            "sku": sku, "name": name,
            "jan": cell("JAN???") or None,
            "manufacturer": cell("?????") or None,
            "brand": cell("?????") or None,
            "in_stock": in_stock,
            "stock_qty": stock,
            "list_price": _to_float(cell("??????")),
            "cost_price": _to_float(cell("????")),
            "spec": spec,
        }
        result.rows.append(row)

    return result


# --------------------------------------------------------------------------
# ??
# --------------------------------------------------------------------------
def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _to_int(v: str) -> int | None:
    v = _digits(v)
    return int(v) if v else None


def _to_float(v: str) -> float | None:
    v = _digits(v, dot=True)
    return float(v) if v else None


def _digits(v: str, dot: bool = False) -> str:
    """??,???????????????????????"""
    if not v:
        return ""
    allowed = "0123456789" + ("." if dot else "")
    return "".join(ch for ch in str(v) if ch in allowed)
