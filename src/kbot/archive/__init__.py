"""Historical archive of the market data (spec 02 §3.9, phase 2, stage E3).

Sealed days are written to immutable Parquet generations, verified against PostgreSQL with
a canonical content fingerprint, and published in the archive tables of ``market``.
"""
