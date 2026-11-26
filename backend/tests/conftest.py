import pytest
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _dns_falso(monkeypatch):
    """Nenhum teste consulta DNS de verdade: por padrão todo host resolve para um IP público.
    Testes de SSRF passam o próprio `resolver`."""
    import app.rede
    monkeypatch.setattr(app.rede, "resolver_padrao", lambda host: ["200.147.67.142"])
