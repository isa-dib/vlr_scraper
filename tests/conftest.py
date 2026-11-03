"""Os testes nunca gravam na pasta de dados real (ex.: o Drive no Colab)."""
import pytest

from vlr import storage as S


@pytest.fixture(autouse=True)
def _pasta_de_dados_temporaria(tmp_path, monkeypatch):
    monkeypatch.setenv("VLR_DADOS", str(tmp_path / "dados"))
    S.configurar(tmp_path / "dados")
    yield
    S._paths = None
