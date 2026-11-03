"""Funções de alto nível para usar no Google Colab (ou em qualquer notebook).

Exemplo (ver colab/coleta_vlr.ipynb):

    from vlr import colab
    colab.preparar("/content/drive/MyDrive/TCC/vlr_dados")
    colab.amostra()                                  # CSVs de exemplo, sem rede
    eventos = colab.listar_eventos()                 # todas as páginas do tier 60
    colab.coletar([2766], max_partidas=2)            # teste curto
    rounds = colab.ler("rounds")                     # DataFrame do parquet
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import crawl as C
from . import storage as S
from .features import DEFAULT_PRICES

PASTA_PADRAO_DRIVE = "/content/drive/MyDrive/TCC/vlr_dados"

_conf = {"min_delay": 3.0, "max_delay": 6.0, "verbose": False}


def em_colab() -> bool:
    try:
        import google.colab  # noqa: F401
        return True
    except ImportError:
        return False


def montar_drive(ponto: str = "/content/drive") -> None:
    """Monta o Google Drive (pede autorização na primeira vez)."""
    if not em_colab():
        print("fora do Colab: nada a montar")
        return
    from google.colab import drive
    if not Path(ponto, "MyDrive").exists():
        drive.mount(ponto)


def preparar(pasta_dados: str | Path = PASTA_PADRAO_DRIVE, min_delay: float = 3.0, max_delay: float = 6.0,
             verbose: bool = False, montar: bool = True) -> S.Paths:
    """Monta o Drive (no Colab), define a pasta de saída e o intervalo entre requisições."""
    if montar and str(pasta_dados).startswith("/content/drive"):
        montar_drive()
    pth = S.configurar(pasta_dados)
    _conf.update(min_delay=min_delay, max_delay=max_delay, verbose=verbose)
    C.configurar_log(verbose, arquivo=False)
    print(f"pasta de dados: {pth.base}")
    for nome in ("raw", "processed", "amostra", "logs"):
        print(f"  {nome:10s} {getattr(pth, nome)}")
    return pth


def _fetcher() -> C.Fetcher:
    return C.Fetcher(_conf["min_delay"], _conf["max_delay"])


def _com_log_em_arquivo():
    dest = C.configurar_log(_conf["verbose"], arquivo=True)
    print(f"log: {dest}")


def amostra(prices_path: Path = DEFAULT_PRICES) -> dict[str, pd.DataFrame]:
    """CSVs de exemplo (2 partidas reais salvas no projeto) em <dados>/amostra."""
    out = C.build_sample(prices_path=prices_path)
    C._print_summary(out, S.paths().amostra, "csv")
    return out


def listar_eventos(paginas: int | None = None, atualizar: bool = False) -> pd.DataFrame:
    df = C.list_events(_fetcher(), paginas, atualizar)
    dest = S.salvar_tabela(df, S.paths().processed, "eventos_concluidos")
    print(f"{len(df)} eventos concluídos -> {dest}")
    return df


def sondar(eventos: list[int] | None = None, paginas: int | None = None) -> pd.DataFrame:
    _com_log_em_arquivo()
    f = _fetcher()
    alvo = eventos if eventos else C.list_events(f, paginas)
    df = C.probe(f, alvo)
    dest = S.salvar_tabela(df, S.paths().processed, "sondagem_replay")
    print(f"-> {dest} ({f.n_requests} requisições)")
    if "replay" in df:
        print(df["replay"].value_counts().to_string())
    return df


def coletar(eventos: list[int], max_partidas: int | None = None, processar_ao_final: bool = True,
            prices_path: Path = DEFAULT_PRICES) -> dict:
    """Baixa os eventos para <dados>/raw (retomável) e, ao final, gera os Parquet."""
    _com_log_em_arquivo()
    resumo = C.collect(_fetcher(), list(eventos), max_partidas)
    if processar_ao_final:
        processar(prices_path)
    return resumo


def processar(prices_path: Path = DEFAULT_PRICES) -> dict[str, pd.DataFrame]:
    out = C.process(prices_path)
    C._print_summary(out, S.paths().processed, "parquet")
    return out


def remover_copias_duplicadas() -> dict:
    """Libera espaço em caches de versões anteriores, que gravavam o 1º mapa duas vezes."""
    return C.remover_copias_duplicadas()


def ler(nome: str) -> pd.DataFrame:
    """Lê uma tabela de <dados>/processed (ex.: 'rounds', 'mapas', 'cobertura_por_evento')."""
    return S.ler_tabela(nome)


def falhas() -> pd.DataFrame:
    p = S.paths().logs / "falhas.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["quando", "etapa", "event_id", "match_id", "erro"])


def uso_de_espaco() -> pd.DataFrame:
    """Tamanho e número de arquivos de cada subpasta de <dados>."""
    rows = []
    pth = S.paths()
    for nome in ("raw", "processed", "amostra", "logs"):
        arqs = [p for p in getattr(pth, nome).rglob("*") if p.is_file()]
        rows.append({"pasta": nome, "arquivos": len(arqs),
                     "MB": round(sum(p.stat().st_size for p in arqs) / 1e6, 1)})
    return pd.DataFrame(rows)


def finalizar() -> None:
    """Garante que tudo foi enviado ao Drive antes de fechar o Colab."""
    if em_colab():
        from google.colab import drive
        drive.flush_and_unmount()
        print("Drive sincronizado e desmontado.")
