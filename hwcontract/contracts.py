"""Discover bundled contract templates and resolve MCP paths without widening ROOT."""
import os
from pathlib import Path

from hwcontract.judge import load_contract
from hwcontract.measure import measures

EXAMPLES = Path(__file__).resolve().parent / "examples"
_SUFFIX = ".contract.yaml"


def bundled_paths():
    # Only direct, regular contract files are trusted. Never follow a package
    # entry symlink to grant access to an arbitrary file outside the root.
    return {p.name: p for p in sorted(EXAMPLES.glob("*" + _SUFFIX))
            if p.is_file() and not p.is_symlink()}


def bundled_contract(name):
    """Installed path of a bundled contract, by filename stem (``dshot``),
    filename (``dshot.contract.yaml``) or the name inside it (``dshot600``).
    None when nothing matches."""
    bundled = bundled_paths()
    filename = name if name.endswith(".yaml") else name + _SUFFIX
    if filename in bundled:
        return str(bundled[filename])
    for path in bundled.values():
        if load_contract(str(path))["contract"] == name:
            return str(path)
    return None


def _measurable(contract):
    try:
        measures(contract)
    except ValueError:
        return False
    return True


def list_contracts():
    """Names use filename stems, matching pytest and Action (e.g. dshot)."""
    entries = []
    for filename, path in bundled_paths().items():
        name = filename[:-len(_SUFFIX)]
        contract = load_contract(str(path))
        kind = contract.get("kind", "timing")
        if kind == "serial":
            adapter = "log file: --check; live: check_serial"
        elif kind == "events":
            adapter = "trace JSON: --check; raw CS/SCK/MOSI edges, see writing-contracts guide"
        elif _measurable(contract):
            adapter = "0/1 CSV: --check --samplerate HZ; live: check_capture (sigrok)"
        else:
            adapter = "observations JSON: --check; edges need measure: to judge a CSV"
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
    found = bundled_contract(value) if value == supplied.name else None
    if found:
        return found
    raise FileNotFoundError(
        f"no contract for {value!r} under {root} or in bundled templates; "
        "use hwcontract --list-contracts or the list_contracts MCP tool")


def cli_contract(value):
    """Shell resolution: the user's own file wherever it is, else a bundled
    name. No root here: a shell user may read their own files. Only a bare
    name (no directory part) falls back to the bundled templates."""
    if os.path.isfile(value) or Path(value).name != value:
        return value
    return resolve_contract(value, os.getcwd())
