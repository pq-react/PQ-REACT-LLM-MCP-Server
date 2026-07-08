"""Quantum-safe compliance policy for the PQ-REACT recommendation engine.

Classifies a parsed crypto component into one of four risk levels:
    vulnerable   — broken under Shor's algorithm: RSA, DSA, DH, ECDH,
                   ECDSA, EdDSA without PQC fallback. These need to be
                   replaced before the regulatory deadlines.
    partial      — hybrid PQC (e.g. ECDH+ML-KEM) — quantum-safe in the
                   transition period because the PQC half holds even if
                   the classical half breaks; counts as "safe enough"
                   for current EU PQC Roadmap milestones (2030 hybrid
                   acceptable; 2035 full PQC).
    safe         — pure PQC (ML-KEM, ML-DSA, SLH-DSA, …) or symmetric
                   primitives that are quantum-resistant at adequate
                   key length (AES-256, SHA-256+, HMAC-SHA-256+).
    unknown      — anything that doesn't pattern-match the above.
                   Defaults to a flag, not a free pass.

The policy is deliberately heuristic + name-based. CycloneDX 1.6 has a
machine-readable `cryptoProperties.algorithmProperties.nistQuantumSecurityLevel`
field; when present and >= 1 we trust it. Otherwise we fall back to
substring matching on the algorithm name.
"""
from __future__ import annotations

import re
from typing import Optional

# PQC algorithm families that count as quantum-safe.
PQC_PATTERNS = (
    "ml-kem", "ml-dsa", "slh-dsa",
    "kyber", "dilithium", "sphincs", "falcon",
    "frodo", "bike", "hqc", "mayo",
)

# Symmetric primitives that are quantum-resistant *at adequate key
# length*. AES-128 is technically vulnerable to Grover's algorithm
# (effective 64-bit security); AES-256 is fine. We over-flag AES-128
# as 'partial' rather than 'safe'.
SAFE_SYMMETRIC = (
    ("aes-256", "safe"),
    ("aes256", "safe"),
    ("aes-192", "safe"),
    ("aes-128", "partial"),
    ("aes128", "partial"),
    ("chacha20", "safe"),
    ("sha-256", "safe"), ("sha256", "safe"),
    ("sha-384", "safe"), ("sha384", "safe"),
    ("sha-512", "safe"), ("sha512", "safe"),
    ("sha3-256", "safe"), ("sha3-384", "safe"), ("sha3-512", "safe"),
    ("hmac-sha-256", "safe"), ("hmac-sha256", "safe"),
    ("hmac-sha-384", "safe"), ("hmac-sha-512", "safe"),
    ("sha-1", "vulnerable"), ("sha1", "vulnerable"),
    ("md5", "vulnerable"),
    ("3des", "vulnerable"), ("des-ede3", "vulnerable"),
    ("rc4", "vulnerable"),
)

VULNERABLE_ASYMMETRIC = (
    "rsa", "dsa", "dh-",
    "ecdh", "ecdsa", "eddsa",
    "ed25519", "ed448",
    "secp", "x25519", "x448",
    "p-256", "p-384", "p-521", "p256", "p384", "p521",
    "brainpool",
)


def classify_quantum_risk(
    name: str,
    nist_quantum_level: Optional[int] = None,
    algorithm_family: Optional[str] = None,
    primitive: Optional[str] = None,
) -> str:
    """Return one of 'vulnerable', 'partial', 'safe', 'unknown'.

    Args:
        name: The full algorithm name as it appears in the CBOM
              (e.g. "RSA-2048", "ML-KEM-768", "X25519+ML-KEM-768").
        nist_quantum_level: CycloneDX cryptoProperties.algorithmProperties
              .nistQuantumSecurityLevel — when >= 1 the algorithm is
              authoritatively quantum-safe by the standardization body.
        algorithm_family: optional CycloneDX field, used as a fallback signal.
        primitive: CycloneDX algorithmProperties.primitive
              ('pke', 'kem', 'signature', 'hash', 'block-cipher', ...).
              Used to disambiguate generic names.
    """
    n = (name or "").lower().strip()
    fam = (algorithm_family or "").lower().strip()
    haystack = f"{n} {fam}"

    # 1. Authoritative signal first
    if nist_quantum_level is not None and nist_quantum_level >= 1:
        return "safe"

    # 2. Hybrid heuristic: a plus sign joining a classical name with a
    #    PQC name → partial (transitional, what NIS2/EU expect today).
    if "+" in n or " plus " in n or "hybrid" in haystack:
        has_pqc = any(p in haystack for p in PQC_PATTERNS)
        has_classical = any(re.search(rf"\b{re.escape(v)}", haystack)
                            for v in VULNERABLE_ASYMMETRIC)
        if has_pqc and has_classical:
            return "partial"
        if has_pqc:
            return "safe"
        # else fall through

    # 3. Pure PQC family match → safe
    if any(p in haystack for p in PQC_PATTERNS):
        return "safe"

    # 4. Symmetric primitive lookup (deterministic table)
    for pat, level in SAFE_SYMMETRIC:
        if pat in haystack:
            return level

    # 5. Vulnerable asymmetric family match
    for v in VULNERABLE_ASYMMETRIC:
        if re.search(rf"\b{re.escape(v)}", haystack):
            return "vulnerable"

    return "unknown"


def evaluate_cbom(parsed_assets: list[dict]) -> dict:
    """Run the policy over a list of parsed assets (output of cyclonedx_parser).

    Returns a summary suitable for the compliance_results table:
        {
            "policy_name": "pqreact-quantum-safe-v1",
            "pass_count":  N,
            "fail_count":  M,
            "findings":    [<top-K vulnerable/partial findings>],
        }

    `pass_count` = assets classified 'safe'.
    `fail_count` = assets classified 'vulnerable'.
    'partial' and 'unknown' don't count toward either — they show up in
    findings as informational.
    """
    passed = 0
    failed = 0
    findings: list[dict] = []
    for a in parsed_assets:
        risk = a.get("quantum_risk_level") or "unknown"
        if risk == "safe":
            passed += 1
        elif risk == "vulnerable":
            failed += 1
            findings.append({
                "rule": "no-quantum-vulnerable-asymmetric",
                "severity": "high",
                "asset": a.get("name"),
                "asset_type": a.get("asset_type"),
                "component_path": a.get("component_path"),
                "message": (f"{a.get('asset_type', 'asset')} {a.get('name')!r} "
                            f"is broken under Shor's algorithm; replace with "
                            f"a NIST-standardized PQC equivalent."),
            })
        elif risk == "partial":
            findings.append({
                "rule": "transitional-hybrid-acceptable",
                "severity": "info",
                "asset": a.get("name"),
                "asset_type": a.get("asset_type"),
                "component_path": a.get("component_path"),
                "message": (f"{a.get('name')!r} is a transitional hybrid; "
                            f"acceptable under EU PQC Roadmap until 2035, "
                            f"after which pure PQC is required."),
            })
        elif risk == "unknown":
            findings.append({
                "rule": "manual-review-required",
                "severity": "low",
                "asset": a.get("name"),
                "asset_type": a.get("asset_type"),
                "component_path": a.get("component_path"),
                "message": (f"Could not classify {a.get('name')!r} "
                            f"automatically; manual review needed."),
            })
    return {
        "policy_name": "pqreact-quantum-safe-v1",
        "pass_count": passed,
        "fail_count": failed,
        "findings": findings,
    }
