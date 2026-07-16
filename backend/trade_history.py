from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

from pydantic import Field

from .models import APIModel


class BulkDeleteTradeLogsRequest(APIModel):
    source: str = Field(pattern="^(live|replay)$")
    ids: list[str] = Field(min_length=1, max_length=500)


def public_trade_log(record: dict[str, Any]) -> dict[str, Any]:
    """Return an export-safe copy without a local screenshot pathname."""
    return {key:value for key,value in record.items() if key != "screenshotPath"}


def _csv_text(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # Numeric cells must remain numeric so exported losses such as -3.25
        # can be summed directly by spreadsheet applications. Formula
        # injection applies to user-controlled strings, not finite numbers.
        return value if math.isfinite(value) else ""
    if isinstance(value, (dict,list)):
        text=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    else:
        text=str(value)
    # Spreadsheet applications interpret these leading characters as formulas.
    if text.lstrip().startswith(("=","+","-","@")):
        text="'"+text
    return text


CSV_COLUMNS = (
    "id","planId","source","direction","state","closedAt","startingEquity",
    "initialQuantityBtc","addQuantityBtc","totalQuantityBtc","initialMargin",
    "addMargin","leverage","grossPnl","fees","slippageUsdt","netPnl",
    "accountReturnPercent",
    "mfeUsdt","maeUsdt","regime4H","addTriggered","returnedToReduceZone",
    "notes","planPrices","actualPrices","actualQuantitiesBtc","executionRisk",
)


def encode_trade_log_csv(records: list[dict[str, Any]]) -> bytes:
    stream=io.StringIO(newline="")
    writer=csv.DictWriter(stream,fieldnames=CSV_COLUMNS,extrasaction="ignore",lineterminator="\r\n")
    writer.writeheader()
    for record in records:
        safe=public_trade_log(record)
        writer.writerow({column:_csv_text(safe.get(column)) for column in CSV_COLUMNS})
    # UTF-8 BOM lets desktop spreadsheet applications recognise Chinese notes.
    return ("\ufeff"+stream.getvalue()).encode("utf-8")


def encode_trade_log_json(source:str,records:list[dict[str,Any]],exported_at:int) -> bytes:
    payload={"source":source,"exportedAt":int(exported_at),"items":[public_trade_log(row) for row in records]}
    return json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode("utf-8")
