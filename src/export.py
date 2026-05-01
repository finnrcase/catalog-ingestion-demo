import io
import pandas as pd

from src.notes import remove_notes_row_prefix


def get_csv_bytes(df: pd.DataFrame) -> bytes:
    df = df.copy()
    df = df.drop(columns=[c for c in ("Local Image Path",) if c in df.columns])
    if "Notes" in df.columns:
        df["Notes"] = df["Notes"].apply(remove_notes_row_prefix)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")
