"""Onde e como os arquivos são gravados.

Pensado para o Google Colab com Google Drive montado, mas funciona igual em
qualquer máquina. Todas as saídas ficam numa única pasta-base:

    <base>/
    ├── raw/          HTML bruto, comprimido (.html.gz)  -> cache da coleta (retomada)
    ├── processed/    tabelas finais em Parquet            -> rounds.parquet, mapas.parquet...
    ├── amostra/      tabelas de amostra em CSV            -> para inspeção rápida
    └── logs/         coleta_<data>.log e falhas.parquet

A pasta-base é, em ordem de prioridade:
1. a passada para `configurar(base)`;
2. a variável de ambiente VLR_DADOS;
3. <projeto>/data (uso local).

Decisões:
* HTML em gzip: as páginas do vlr.gg têm de 300 a 850 KB e ficam ~8 a 10x menores
  comprimidas. Isso importa no Drive (cota de 15 GB no plano gratuito) e reduz o
  número de bytes trafegados pelo FUSE do Drive.
* Gravação atômica: escreve num arquivo temporário na mesma pasta e só depois
  renomeia (os.replace). Se o Colab cair no meio da gravação, nunca fica um
  arquivo pela metade com o nome final — e a retomada da coleta confia nisso.
* Parquet (zstd) para as tabelas: arquivo colunar, tipado e bem menor que CSV;
  as amostras continuam em CSV para abrir direto no Sheets/Excel.
"""
from __future__ import annotations

import gzip
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    base: Path

    @property
    def raw(self) -> Path:
        return self.base / "raw"

    @property
    def processed(self) -> Path:
        return self.base / "processed"

    @property
    def amostra(self) -> Path:
        return self.base / "amostra"

    @property
    def logs(self) -> Path:
        return self.base / "logs"

    def criar(self) -> "Paths":
        for p in (self.raw, self.processed, self.amostra, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self


_paths: Paths | None = None


def configurar(base: str | Path | None = None) -> Paths:
    """Define a pasta-base de todas as saídas e cria as subpastas."""
    global _paths
    if base is None:
        base = os.environ.get("VLR_DADOS") or (PROJECT_ROOT / "data")
    _paths = Paths(Path(base).expanduser().resolve()).criar()
    return _paths


def paths() -> Paths:
    return _paths if _paths is not None else configurar()


# ----------------------------------------------------------------------------
# gravação atômica
# ----------------------------------------------------------------------------

def _atomic_write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp_", suffix=dest.suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----------------------------------------------------------------------------
# cache de HTML bruto
# ----------------------------------------------------------------------------

class RawStore:
    """Cache de páginas. Chaves são caminhos relativos como 'event_1/match_2/logs.html'.

    Grava sempre '<chave>.gz'. Na leitura aceita também o arquivo sem compressão
    (usado pela amostra e por caches antigos, de antes desta versão).
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _gz(self, key: str) -> Path:
        return self.root / f"{key}.gz"

    def _plain(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._gz(key).exists() or self._plain(key).exists()

    def read(self, key: str) -> str:
        gz = self._gz(key)
        if gz.exists():
            return gzip.decompress(gz.read_bytes()).decode("utf-8")
        return self._plain(key).read_text(encoding="utf-8")

    def write(self, key: str, text: str) -> None:
        _atomic_write_bytes(self._gz(key), gzip.compress(text.encode("utf-8"), compresslevel=6))

    def event_dirs(self):
        return sorted(p for p in self.root.glob("event_*") if p.is_dir())

    def match_dirs(self, event_dir: Path):
        return sorted(p for p in event_dir.glob("match_*") if p.is_dir())

    def key_of(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def limpar_temporarios(self) -> int:
        """Remove sobras de gravações interrompidas (.tmp_*). Retorna quantas removeu."""
        n = 0
        for p in self.root.rglob(".tmp_*"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n


# ----------------------------------------------------------------------------
# tabelas
# ----------------------------------------------------------------------------

def _limpar_para_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Colunas de objeto com tipos mistos viram texto, para o pyarrow aceitar."""
    df = df.copy()
    for c in df.columns:
        if df[c].dtype == object:
            tipos = {type(v) for v in df[c].dropna()}
            if len(tipos) > 1:
                df[c] = df[c].map(lambda v: v if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    return df


def salvar_tabela(df: pd.DataFrame, pasta: Path, nome: str, formato: str = "parquet") -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    if formato == "parquet":
        dest = pasta / f"{nome}.parquet"
        fd, tmp = tempfile.mkstemp(dir=pasta, prefix=".tmp_", suffix=".parquet")
        os.close(fd)
        try:
            _limpar_para_parquet(df).to_parquet(tmp, index=False, compression="zstd")
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    elif formato == "csv":
        dest = pasta / f"{nome}.csv"
        _atomic_write_bytes(dest, df.to_csv(index=False).encode("utf-8"))
    else:
        raise ValueError(f"formato desconhecido: {formato}")
    return dest


def ler_tabela(nome: str, pasta: Path | None = None) -> pd.DataFrame:
    """Lê uma tabela de processed/ (parquet) — atalho para o notebook."""
    pasta = paths().processed if pasta is None else Path(pasta)
    pq = pasta / f"{nome}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)
    return pd.read_csv(pasta / f"{nome}.csv")


def anexar_tabela(df: pd.DataFrame, pasta: Path, nome: str) -> Path:
    """Acrescenta linhas a um parquet existente (usado para o registro de falhas)."""
    dest = pasta / f"{nome}.parquet"
    if dest.exists():
        df = pd.concat([pd.read_parquet(dest), df], ignore_index=True)
    return salvar_tabela(df, pasta, nome, "parquet")
