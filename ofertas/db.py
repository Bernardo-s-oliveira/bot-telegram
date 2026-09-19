import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from .config import DATA_DIR
from .models import Oferta

_DB = DATA_DIR / "ofertas.db"

# Histórico de preços: só grava quando o preço muda ou passou o "batimento" abaixo,
# para o banco não crescer a cada ciclo. Snapshots antigos são podados.
_MUDANCA_MINIMA = 0.005          # 0,5% conta como mudança de preço
_BATIMENTO = dt.timedelta(hours=6)
_RETENCAO_DIAS = 120


@contextmanager
def _conn():
    c = sqlite3.connect(_DB)
    try:
        c.execute(
            "CREATE TABLE IF NOT EXISTS postadas ("
            " uid TEXT PRIMARY KEY,"
            " plataforma TEXT,"
            " titulo TEXT,"
            " preco REAL,"
            " postada_em TEXT)"
        )
        c.execute("CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS precos (uid TEXT NOT NULL, preco REAL NOT NULL, em TEXT NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_precos_uid ON precos (uid, em)")
        yield c
        c.commit()
    finally:
        c.close()


def ultima_postagem(uid: str) -> tuple[dt.datetime, float | None] | None:
    """(quando, preço na época) da última postagem do produto; None se nunca postado."""
    with _conn() as c:
        row = c.execute("SELECT postada_em, preco FROM postadas WHERE uid = ?", (uid,)).fetchone()
    return (dt.datetime.fromisoformat(row[0]), row[1]) if row else None


def ultimas_postagens(uids: list[str]) -> dict[str, tuple[dt.datetime, float | None]]:
    """Versão em lote de `ultima_postagem`: {uid: (quando, preço)} só dos já postados."""
    resultado: dict[str, tuple[dt.datetime, float | None]] = {}
    with _conn() as c:
        for i in range(0, len(uids), 500):
            lote = uids[i:i + 500]
            marcas = ",".join("?" * len(lote))
            for uid, em, preco in c.execute(
                    f"SELECT uid, postada_em, preco FROM postadas WHERE uid IN ({marcas})", lote):
                resultado[uid] = (dt.datetime.fromisoformat(em), preco)
    return resultado


def ja_postada(uid: str, dentro_de_dias: int) -> bool:
    ultima = ultima_postagem(uid)
    return bool(ultima) and (dt.datetime.now() - ultima[0]) < dt.timedelta(days=dentro_de_dias)


def registrar(oferta: Oferta) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO postadas (uid, plataforma, titulo, preco, postada_em)"
            " VALUES (?, ?, ?, ?, ?)",
            (oferta.uid, oferta.plataforma, oferta.titulo, oferta.preco,
             dt.datetime.now().isoformat(timespec="seconds")),
        )


def titulos_postados_desde(horas: float, agora: dt.datetime | None = None) -> list[str]:
    """Títulos postados nas últimas `horas` (para a regra de variedade)."""
    desde = ((agora or dt.datetime.now()) - dt.timedelta(hours=horas)).isoformat(timespec="seconds")
    with _conn() as c:
        return [t for (t,) in c.execute("SELECT titulo FROM postadas WHERE postada_em >= ?", (desde,))]


def ler_estado(chave: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT valor FROM estado WHERE chave = ?", (chave,)).fetchone()
    return row[0] if row else None


def gravar_estado(chave: str, valor: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO estado (chave, valor) VALUES (?, ?)", (chave, valor))


def total_postadas() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM postadas").fetchone()[0]


# ── Histórico de preços ──────────────────────────────────────────────

def registrar_precos(ofertas: list[Oferta], agora: dt.datetime | None = None) -> int:
    """Grava o preço de cada oferta coletada (se mudou ou passou o batimento). Retorna nº gravado."""
    agora = agora or dt.datetime.now()
    gravados = 0
    with _conn() as c:
        for o in ofertas:
            if not o.preco or o.preco <= 0:
                continue
            ultimo = c.execute("SELECT preco, em FROM precos WHERE uid = ? ORDER BY em DESC LIMIT 1",
                               (o.uid,)).fetchone()
            if ultimo:
                mudou = abs(o.preco - ultimo[0]) > ultimo[0] * _MUDANCA_MINIMA
                if not mudou and agora - dt.datetime.fromisoformat(ultimo[1]) < _BATIMENTO:
                    continue
            c.execute("INSERT INTO precos (uid, preco, em) VALUES (?, ?, ?)",
                      (o.uid, o.preco, agora.isoformat(timespec="seconds")))
            gravados += 1
        c.execute("DELETE FROM precos WHERE em < ?",
                  ((agora - dt.timedelta(days=_RETENCAO_DIAS)).isoformat(timespec="seconds"),))
    return gravados


@dataclass
class Historico:
    minimo: float          # menor preço no período
    referencia: float      # preço "normal": mediana ponderada pelo tempo
    amostras: int          # nº de preços gravados no período
    dias: float            # quantos dias o histórico cobre (do 1º registro até agora)

    def suficiente(self, min_amostras: int = 4, min_dias: float = 1.0) -> bool:
        return self.amostras >= min_amostras and self.dias >= min_dias


def _mediana_ponderada(pontos: list[tuple[dt.datetime, float]], fim: dt.datetime) -> float:
    """Cada preço vale até o próximo registro (o último, até `fim`); mediana pelo tempo de duração."""
    pesos = []
    for i, (t, p) in enumerate(pontos):
        proximo = pontos[i + 1][0] if i + 1 < len(pontos) else fim
        pesos.append((p, max((proximo - t).total_seconds(), 1.0)))
    pesos.sort()
    metade = sum(w for _, w in pesos) / 2
    acumulado = 0.0
    for p, w in pesos:
        acumulado += w
        if acumulado >= metade:
            return p
    return pesos[-1][0]


def historico(uids: list[str], dias: int = 30, agora: dt.datetime | None = None) -> dict[str, Historico]:
    """Estatísticas de preço dos últimos `dias` para cada uid que tem registros."""
    agora = agora or dt.datetime.now()
    desde = (agora - dt.timedelta(days=dias)).isoformat(timespec="seconds")
    por_uid: dict[str, list[tuple[dt.datetime, float]]] = {}
    with _conn() as c:
        for i in range(0, len(uids), 500):  # limite de variáveis do SQLite
            lote = uids[i:i + 500]
            marcas = ",".join("?" * len(lote))
            for uid, preco, em in c.execute(
                    f"SELECT uid, preco, em FROM precos WHERE em >= ? AND uid IN ({marcas}) ORDER BY em",
                    (desde, *lote)):
                por_uid.setdefault(uid, []).append((dt.datetime.fromisoformat(em), preco))
    return {
        uid: Historico(
            minimo=min(p for _, p in pontos),
            referencia=_mediana_ponderada(pontos, agora),
            amostras=len(pontos),
            dias=(agora - pontos[0][0]).total_seconds() / 86400,
        )
        for uid, pontos in por_uid.items()
    }
