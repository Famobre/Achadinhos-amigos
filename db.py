"""Banco local (SQLite): posts feitos, ganhos e log."""
import sqlite3
import threading
import time
from datetime import datetime
from config import PASTA

ARQ = PASTA / "dados.db"
DIAS_SEM_REPETIR = 7


class DB:
    def __init__(self, path=ARQ):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                produto_id TEXT, plataforma TEXT, nome TEXT, preco REAL,
                desconto REAL, imagem TEXT, link TEXT, postado_em INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_posts_prod ON posts(produto_id);
            CREATE TABLE IF NOT EXISTS ganhos (
                id TEXT PRIMARY KEY, plataforma TEXT, valor REAL,
                descricao TEXT, data_ts INTEGER, origem TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, nivel TEXT, msg TEXT
            );
            """)
            self.conn.commit()

    def _q(self, sql, args=(), commit=False):
        with self.lock:
            cur = self.conn.execute(sql, args)
            if commit:
                self.conn.commit()
            return cur.fetchall()

    # ---------- posts ----------
    def ja_postado(self, produto_id) -> bool:
        limite = int(time.time()) - DIAS_SEM_REPETIR * 86400
        return bool(self._q("SELECT 1 FROM posts WHERE produto_id=? AND postado_em>? LIMIT 1", (produto_id, limite)))

    def registrar_post(self, o: dict):
        self._q(
            "INSERT INTO posts (produto_id, plataforma, nome, preco, desconto, imagem, link, postado_em) VALUES (?,?,?,?,?,?,?,?)",
            (o["id"], o["plataforma"], o["nome"], o["preco"], o.get("desconto", 0), o.get("imagem"), o["link"], int(time.time())),
            commit=True,
        )

    def posts_ultimas_24h(self) -> int:
        return self._q("SELECT COUNT(*) c FROM posts WHERE postado_em>?", (int(time.time()) - 86400,))[0]["c"]

    def posts_hoje(self) -> int:
        from datetime import datetime
        meia_noite = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        return self._q("SELECT COUNT(*) c FROM posts WHERE postado_em>=?", (meia_noite,))[0]["c"]

    def ultimos_posts(self, n=20):
        return [dict(r) for r in self._q("SELECT * FROM posts ORDER BY postado_em DESC LIMIT ?", (n,))]

    def contagem_posts(self):
        hoje = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        rows = self._q("""SELECT plataforma, COUNT(*) total,
                          SUM(CASE WHEN postado_em>=? THEN 1 ELSE 0 END) hoje
                          FROM posts GROUP BY plataforma""", (hoje,))
        return {r["plataforma"]: {"total": r["total"], "hoje": r["hoje"] or 0} for r in rows}

    # ---------- ganhos ----------
    def salvar_ganho(self, id_, plataforma, valor, descricao, data_ts, origem):
        self._q("INSERT OR REPLACE INTO ganhos VALUES (?,?,?,?,?,?)",
                (id_, plataforma, valor, descricao, data_ts, origem), commit=True)

    def apagar_ganho(self, id_):
        self._q("DELETE FROM ganhos WHERE id=? AND origem='manual'", (id_,), commit=True)

    def resumo_ganhos(self):
        agora = datetime.now()
        hoje = int(agora.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        mes = int(agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
        r = self._q("""SELECT
            COALESCE(SUM(valor),0) total,
            COALESCE(SUM(CASE WHEN data_ts>=? THEN valor END),0) mes,
            COALESCE(SUM(CASE WHEN data_ts>=? THEN valor END),0) hoje,
            COALESCE(SUM(CASE WHEN plataforma='shopee' THEN valor END),0) shopee,
            COALESCE(SUM(CASE WHEN plataforma='mercadolivre' THEN valor END),0) mercadolivre,
            COALESCE(SUM(CASE WHEN plataforma='aliexpress' THEN valor END),0) aliexpress,
            COALESCE(SUM(CASE WHEN plataforma='amazon' THEN valor END),0) amazon,
            COUNT(*) vendas
            FROM ganhos""", (mes, hoje))[0]
        return dict(r)

    def ganhos_por_dia(self, dias=30):
        inicio = int(time.time()) - dias * 86400
        rows = self._q("""SELECT date(data_ts,'unixepoch','localtime') dia, plataforma, SUM(valor) v
                          FROM ganhos WHERE data_ts>=? GROUP BY dia, plataforma ORDER BY dia""", (inicio,))
        return [dict(r) for r in rows]

    def ultimos_ganhos(self, n=30):
        return [dict(r) for r in self._q("SELECT * FROM ganhos ORDER BY data_ts DESC LIMIT ?", (n,))]

    # ---------- log ----------
    def log(self, msg, nivel="info"):
        try:  # o console do Windows não imprime emoji/setas; o log no painel continua completo
            print(f"[{datetime.now():%H:%M:%S}] {msg}")
        except Exception:
            pass
        self._q("INSERT INTO logs (ts, nivel, msg) VALUES (?,?,?)", (int(time.time()), nivel, msg), commit=True)
        self._q("DELETE FROM logs WHERE id < (SELECT MAX(id) - 500 FROM logs)", commit=True)

    def ultimos_logs(self, n=60):
        return [dict(r) for r in self._q("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (n,))]
