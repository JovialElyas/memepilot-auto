"""Règles d'architecture (ARCHITECTURE §2, SPEC F-13/F-30).

- Seul le paquet memepilot.execution peut importer un client de trading.
- Aucun module ne lit GMGN_API_KEY / GMGN_PRIVATE_KEY (gmgn-cli s'en charge).
- Aucun appel subprocess avec shell=True.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "memepilot"
TRADE_MODULES = ("memepilot.gmgn.trade_client",)


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def test_only_execution_imports_trade_client() -> None:
    offenders = []
    for f in _py_files():
        rel = f.relative_to(SRC).parts
        if rel[0] in ("execution",) or f.name == "trade_client.py":
            continue
        if any(i.startswith(TRADE_MODULES) for i in _imports(f)):
            offenders.append(str(f))
    assert not offenders, f"import interdit du client de trading : {offenders}"


def test_no_secret_env_reads() -> None:
    allowed = {"logging_utils.py"}  # ne fait que masquer
    offenders = [
        str(f)
        for f in _py_files()
        if f.name not in allowed
        and any(s in f.read_text(encoding="utf-8") for s in ("GMGN_API_KEY", "GMGN_PRIVATE_KEY"))
    ]
    assert not offenders, offenders


def test_no_shell_true() -> None:
    offenders = []
    for f in _py_files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "shell"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                offenders.append(str(f))
    assert not offenders, offenders


PURE_FORBIDDEN = ("sqlite3", "asyncio", "subprocess", "socket", "httpx", "requests", "anthropic",
                  "memepilot.gmgn", "memepilot.engine", "memepilot.risk.store")


def test_guardian_and_signals_are_pure() -> None:
    """Le Guardian, l'état de risque, les signaux et la stratégie : aucune I/O, aucun réseau, aucun LLM."""
    targets = [SRC / "risk" / "guardian.py", SRC / "risk" / "state.py", *sorted((SRC / "signals").glob("*.py")),
               *sorted((SRC / "strategy").glob("*.py"))]
    offenders = {
        str(f): sorted(
            i for i in _imports(f) if i.startswith(PURE_FORBIDDEN) and not i.startswith("memepilot.gmgn.models")
        )
        for f in targets
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, offenders


def test_lock_can_only_be_lifted_by_admin_script() -> None:
    callers = [
        str(f) for f in _py_files()
        if f.name != "store.py" and "admin_unlock(" in f.read_text(encoding="utf-8")
    ]
    assert not callers, callers
    assert "admin_unlock(" in (SRC.parents[1] / "scripts" / "risk_admin.py").read_text(encoding="utf-8")


def test_no_code_path_relaxes_limits() -> None:
    """Aucun module ne réécrit le fichier de limites ni ne construit des Limits à partir d'un état."""
    offenders = [str(f) for f in _py_files()
                 if f.name != "limits.py" and ("limits.yaml" in f.read_text(encoding="utf-8")
                                                and "write" in f.read_text(encoding="utf-8"))]
    assert not offenders, offenders
