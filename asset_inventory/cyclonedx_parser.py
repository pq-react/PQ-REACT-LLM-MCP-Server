"""Parse CycloneDX 1.6 CBOMs into rows for the crypto_assets table.

CBOMkit-theia and CBOMkit's main scanner both emit CycloneDX 1.6 with
the cryptoProperties extension. The shape we care about:

    {
      "bomFormat": "CycloneDX",
      "specVersion": "1.6",
      "components": [
        {
          "type": "cryptographic-asset",
          "name": "RSA-2048",
          "version": "...",
          "cryptoProperties": {
            "assetType": "algorithm",
            "algorithmProperties": {
              "primitive": "pke",
              "parameterSetIdentifier": "2048",
              "nistQuantumSecurityLevel": 0,
              ...
            }
          },
          "evidence": {
            "occurrences": [{"location": "/etc/ssl/cert.pem"}]
          }
        }
      ]
    }

Older CBOMs (pre-1.6) just have plain components without cryptoProperties.
We handle both, fall back to the policy module for risk classification
when machine-readable signals aren't there.
"""
from __future__ import annotations

from typing import Any, Optional

from policy import classify_quantum_risk

# CycloneDX assetType values map to our crypto_assets.asset_type column.
# We squash the long list into the 5 buckets the schema accepts.
_ASSET_TYPE_MAP = {
    "algorithm":         "algorithm",
    "certificate":       "certificate",
    "private-key":       "key",
    "public-key":        "key",
    "secret-key":        "key",
    "key":               "key",
    "protocol":          "protocol",
    "related-crypto-material": "key",
    "library":           "library",
    "kem":               "algorithm",
    "signature":         "algorithm",
}


def _first_occurrence(component: dict) -> Optional[str]:
    """Pull the first 'evidence.occurrences[].location' out of a component."""
    ev = component.get("evidence") or {}
    occs = ev.get("occurrences") or []
    for o in occs:
        if isinstance(o, dict) and o.get("location"):
            return str(o["location"])[:512]
    return None


def _norm_asset_type(raw: Optional[str], component_type: Optional[str]) -> str:
    """Map a CycloneDX assetType / component type to our 5-bucket column."""
    if raw and raw.lower() in _ASSET_TYPE_MAP:
        return _ASSET_TYPE_MAP[raw.lower()]
    if component_type == "library":
        return "library"
    return "algorithm"


def parse(cbom: dict, sector: Optional[str] = None) -> list[dict]:
    """Walk a CycloneDX 1.6 dict and return a list of crypto_assets rows.

    Each row dict is shaped to match the crypto_assets columns:
        {asset_type, name, version, quantum_risk_level,
         component_path, sector}

    Caller is responsible for adding cbom_doc_id before INSERT.
    """
    rows: list[dict] = []
    components = cbom.get("components") or []
    for c in components:
        if not isinstance(c, dict):
            continue
        ctype = c.get("type")
        # Accept components flagged crypto-asset OR ones with cryptoProperties
        cprops = c.get("cryptoProperties") or {}
        if ctype != "cryptographic-asset" and not cprops:
            # Skip non-crypto components (libraries that aren't crypto, etc.)
            # — but include 'library' with crypto in the name as a hint.
            name_l = (c.get("name") or "").lower()
            if "crypt" not in name_l and "tls" not in name_l \
               and "ssl" not in name_l and "cipher" not in name_l:
                continue

        name = c.get("name") or "unknown"
        version = c.get("version")
        asset_type_raw = (cprops.get("assetType") or "")
        asset_type = _norm_asset_type(asset_type_raw, ctype)

        algoprops = cprops.get("algorithmProperties") or {}
        nist_level = algoprops.get("nistQuantumSecurityLevel")
        primitive = algoprops.get("primitive")
        family = algoprops.get("family") or algoprops.get("algorithmFamily")

        # If this is an algorithm, its full description (e.g. "RSA-2048")
        # often includes the parameter set; reconstruct when only base
        # name is given.
        param = algoprops.get("parameterSetIdentifier")
        full_name = name
        if param and not str(param) in name:
            full_name = f"{name}-{param}"

        try:
            nist_int = int(nist_level) if nist_level is not None else None
        except (TypeError, ValueError):
            nist_int = None

        risk = classify_quantum_risk(
            name=full_name,
            nist_quantum_level=nist_int,
            algorithm_family=str(family) if family else None,
            primitive=str(primitive) if primitive else None,
        )

        rows.append({
            "asset_type":         asset_type,
            "name":               full_name[:255],
            "version":            (str(version)[:64] if version else None),
            "quantum_risk_level": risk,
            "component_path":     _first_occurrence(c),
            "sector":             sector,
        })
    return rows


def cbom_metadata(cbom: dict) -> dict:
    """Pull source_system / scan_tool / scan_date from CycloneDX metadata.
    Defaults to safe placeholders when fields are missing."""
    md = cbom.get("metadata") or {}
    timestamp = md.get("timestamp")
    tools_raw = md.get("tools") or []
    # CycloneDX 1.6 nests tools under .components or .services
    if isinstance(tools_raw, dict):
        tools_raw = (tools_raw.get("components") or [])
    tool_name = "unknown"
    for t in tools_raw:
        if isinstance(t, dict):
            n = t.get("name") or t.get("vendor")
            if n:
                tool_name = str(n)[:64]
                break
    component_target = md.get("component") or {}
    if isinstance(component_target, dict):
        source = component_target.get("name") or component_target.get("bom-ref")
    else:
        source = None
    return {
        "source_system": (str(source)[:128] if source else "unknown"),
        "scan_tool":     tool_name,
        "scan_timestamp": timestamp,   # ISO-8601 string or None; caller maps to DATETIME
    }
