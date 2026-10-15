"""Discover bundled contract templates and resolve MCP paths without widening ROOT."""
from pathlib import Path

from hwcontract.judge import load_contract

EXAMPLES = Path(__file__).resolve().parent / "examples"
_SUFFIX = ".contract.yaml"
_LED = {"ws2812", "ws2812b", "ws2812b_mini", "ws2813", "sk6812"}


def bundled_paths():
    # Only direct, regular contract files are trusted. Never follow a package
    # entry symlink to grant access to an arbitrary file outside the root.
    return {p.name: p for p in sorted(EXAMPLES.glob("*" + _SUFFIX))
            if p.is_file() and not p.is_symlink()}


def list_contracts():
    """Names use filename stems, matching pytest and Action (e.g. dshot)."""
    entries = []
    for filename, path in bundled_paths().items():
        name = filename[:-len(_SUFFIX)]
        contract = load_contract(str(path))
        kind = contract.get("kind", "timing")
        if kind == "serial":
            adapter = "serial log replay / check_serial"
        elif kind == "events":
            adapter = "raw CS/SCK/MOSI edges; see writing-contracts guide"
        elif name in _LED:
            adapter = "WS2812-family pulse buckets / check_ws2812; verify thresholds"
        elif name == "dshot":
            adapter = "DShot600 pulse buckets / check_dshot"
        elif name.startswith("dshot"):
            adapter = "pulse buckets with custom rate-specific thresholds (not check_dshot)"
        else:
            adapter = "custom observation adapter required"
        entries.append({"name": name, "contract": contract["contract"],
                        "kind": kind, "path": str(path), "capture_path": adapter})
    return entries


def resolve_contract(value, root):
    """Existing project file, then bundled short name or exact installed path.

    All arbitrary paths stay under root after symlink resolution. A name may
    be ``ws2812b`` or ``ws2812b.contract.yaml``. Only an exact allowlisted
    package path can bypass root; basenames of other absolute paths cannot.
    """
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("contract_path must be a non-empty path or bundled name")
    root = Path(root).resolve()
    supplied = Path(value)
    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = candidate.resolve()
    bundled = bundled_paths()
    if value in {str(p) for p in bundled.values()}:
        return value
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes allowed root: {value}")
    # lexists semantics: even a broken local symlink must not silently select
    # a different bundled contract instead of the user's intended file.
    if candidate.exists() or candidate.is_symlink():
        if not candidate.is_file():
            raise ValueError(f"contract path is not a regular file: {value}")
        return str(resolved)
    if value == supplied.name:
        filename = value if value.endswith(".yaml") else value + _SUFFIX
        if filename in bundled:
            return str(bundled[filename])
    raise FileNotFoundError(
        f"no contract for {value!r} under {root} or in bundled templates; "
        "use hwcontract --list-contracts or the list_contracts MCP tool")
