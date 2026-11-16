"""Loja da Mãe: anúncios + grupos do Facebook + fila de postagem assistida.

Nada aqui loga no Facebook nem posta sozinho. O launcher só organiza a fila,
avisa a hora, copia o texto, abre a pasta das fotos e abre o grupo no navegador.
Quem publica é ela.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from config import PASTA

PASTA_FOTOS = PASTA / "fotos_loja"


def _inicio_do_dia() -> int:
    return int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def copiar_texto(texto: str):
    if sys.platform == "win32":
        subprocess.run("clip", input=texto.encode("utf-16"), check=True, shell=True)
    else:  # só pra testes fora do Windows
        (PASTA / "_clipboard.txt").write_text(texto, encoding="utf-8")


def abrir_pasta(caminho: Path):
    if sys.platform == "win32":
        os.startfile(str(caminho))


class Loja:
    def __init__(self, db):
        self.db = db
        PASTA_FOTOS.mkdir(exist_ok=True)
        with db.lock:
            db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS loja_grupos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, url TEXT, criado INTEGER
            );
            CREATE TABLE IF NOT EXISTS loja_anuncios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, texto TEXT, fotos TEXT, criado INTEGER
            );
            CREATE TABLE IF NOT EXISTS loja_fila (
                id INTEGER PRIMARY KEY AUTOINCREMENT, anuncio_id INTEGER, grupo_id INTEGER,
                status TEXT DEFAULT 'pendente', agendado_ts INTEGER, postado_ts INTEGER
            );
            """)
            db.conn.commit()

    # ---------------- grupos ----------------
    def grupos(self):
        hoje = _inicio_do_dia()
        rows = self.db._q("""
            SELECT g.*, (SELECT MAX(postado_ts) FROM loja_fila f
                         WHERE f.grupo_id=g.id AND f.status='postado') ultimo_post
            FROM loja_grupos g ORDER BY g.nome COLLATE NOCASE""")
        out = []
        for r in rows:
            d = dict(r)
            d["postado_hoje"] = bool(d["ultimo_post"] and d["ultimo_post"] >= hoje)
            out.append(d)
        return out

    def salvar_grupo(self, nome: str, url: str):
        nome, url = (nome or "").strip(), (url or "").strip()
        if not nome or "facebook.com" not in url:
            return {"ok": False, "msg": "Coloque um nome e um link do Facebook (facebook.com/groups/...)"}
        if not url.startswith("http"):
            url = "https://" + url
        self.db._q("INSERT INTO loja_grupos (nome, url, criado) VALUES (?,?,?)",
                   (nome, url, int(time.time())), commit=True)
        return {"ok": True, "msg": f"Grupo \"{nome}\" adicionado"}

    def apagar_grupo(self, gid: int):
        self.db._q("DELETE FROM loja_grupos WHERE id=?", (gid,), commit=True)
        self.db._q("DELETE FROM loja_fila WHERE grupo_id=? AND status='pendente'", (gid,), commit=True)
        return {"ok": True, "msg": "Grupo removido"}

    # ---------------- anúncios ----------------
    def anuncios(self):
        out = []
        for r in self.db._q("SELECT * FROM loja_anuncios ORDER BY criado DESC"):
            d = dict(r)
            d["fotos"] = self._fotos_info(json.loads(d["fotos"] or "[]"))
            out.append(d)
        return out

    def _fotos_info(self, nomes):
        return [{"nome": n, "uri": (PASTA_FOTOS / n).as_uri()} for n in nomes if (PASTA_FOTOS / n).exists()]

    def importar_fotos(self, caminhos):
        """Copia as fotos escolhidas pra pasta da loja. Retorna os nomes novos."""
        nomes = []
        for c in caminhos or []:
            p = Path(c)
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            novo = f"{uuid.uuid4().hex[:8]}{p.suffix.lower()}"
            shutil.copy2(p, PASTA_FOTOS / novo)
            nomes.append(novo)
        return self._fotos_info(nomes)

    def salvar_anuncio(self, titulo: str, texto: str, fotos: list, aid: int | None = None):
        titulo, texto = (titulo or "").strip(), (texto or "").strip()
        if not texto:
            return {"ok": False, "msg": "Escreva o texto do anúncio"}
        titulo = titulo or texto.splitlines()[0][:40]
        fotos_json = json.dumps([f for f in fotos or [] if (PASTA_FOTOS / f).exists()])
        if aid:
            self.db._q("UPDATE loja_anuncios SET titulo=?, texto=?, fotos=? WHERE id=?",
                       (titulo, texto, fotos_json, aid), commit=True)
        else:
            self.db._q("INSERT INTO loja_anuncios (titulo, texto, fotos, criado) VALUES (?,?,?,?)",
                       (titulo, texto, fotos_json, int(time.time())), commit=True)
        return {"ok": True, "msg": "Anúncio salvo"}

    def apagar_anuncio(self, aid: int):
        self.db._q("DELETE FROM loja_anuncios WHERE id=?", (aid,), commit=True)
        self.db._q("DELETE FROM loja_fila WHERE anuncio_id=? AND status='pendente'", (aid,), commit=True)
        return {"ok": True, "msg": "Anúncio apagado"}

    # ---------------- fila ----------------
    def montar_fila(self, anuncio_id: int, grupo_ids: list, intervalo_min: int):
        intervalo = max(5, int(intervalo_min or 10)) * 60
        grupos = {g["id"]: g for g in self.grupos()}
        pendentes = {r["grupo_id"] for r in self.db._q("SELECT grupo_id FROM loja_fila WHERE status='pendente'")}
        ultimo = self.db._q("SELECT MAX(agendado_ts) m FROM loja_fila WHERE status='pendente'")[0]["m"]
        t = max(int(time.time()), (ultimo or 0) + intervalo)
        adicionados, pulados = 0, []
        for gid in grupo_ids:
            g = grupos.get(int(gid))
            if not g:
                continue
            if g["postado_hoje"] or g["id"] in pendentes:
                pulados.append(g["nome"])
                continue
            self.db._q("INSERT INTO loja_fila (anuncio_id, grupo_id, agendado_ts) VALUES (?,?,?)",
                       (anuncio_id, g["id"], t), commit=True)
            t += intervalo
            adicionados += 1
        msg = f"{adicionados} grupo(s) na fila"
        if pulados:
            msg += f" — pulei {len(pulados)} que já tem post hoje ou já estão na fila"
        return {"ok": adicionados > 0, "msg": msg}

    def fila(self):
        rows = self.db._q("""
            SELECT f.*, g.nome grupo, g.url, a.titulo, a.texto, a.fotos
            FROM loja_fila f JOIN loja_grupos g ON g.id=f.grupo_id JOIN loja_anuncios a ON a.id=f.anuncio_id
            WHERE f.status='pendente' ORDER BY f.agendado_ts""")
        out = []
        for r in rows:
            d = dict(r)
            d["fotos"] = self._fotos_info(json.loads(d["fotos"] or "[]"))
            out.append(d)
        return out

    def historico(self, n=30):
        rows = self.db._q("""
            SELECT f.postado_ts, f.status, g.nome grupo, a.titulo
            FROM loja_fila f JOIN loja_grupos g ON g.id=f.grupo_id JOIN loja_anuncios a ON a.id=f.anuncio_id
            WHERE f.status!='pendente' ORDER BY f.postado_ts DESC LIMIT ?""", (n,))
        return [dict(r) for r in rows]

    def preparar(self, fid: int):
        """Copia o texto, abre a pasta com as fotos do anúncio e abre o grupo."""
        rows = self.db._q("""SELECT f.id, g.url, a.texto, a.fotos FROM loja_fila f
                             JOIN loja_grupos g ON g.id=f.grupo_id JOIN loja_anuncios a ON a.id=f.anuncio_id
                             WHERE f.id=?""", (fid,))
        if not rows:
            return {"ok": False, "msg": "Item não está mais na fila"}
        r = rows[0]
        copiar_texto(r["texto"])
        fotos = json.loads(r["fotos"] or "[]")
        if fotos:
            pasta = PASTA_FOTOS / "_postar_agora"
            shutil.rmtree(pasta, ignore_errors=True)
            pasta.mkdir()
            for f in fotos:
                if (PASTA_FOTOS / f).exists():
                    shutil.copy2(PASTA_FOTOS / f, pasta / f)
            abrir_pasta(pasta)
        webbrowser.open(r["url"])
        return {"ok": True, "msg": "Texto copiado! Cole no grupo (Ctrl+V) e arraste as fotos da pasta que abriu."}

    def concluir(self, fid: int, status: str = "postado"):
        status = "postado" if status == "postado" else "pulado"
        self.db._q("UPDATE loja_fila SET status=?, postado_ts=? WHERE id=?",
                   (status, int(time.time()), fid), commit=True)
        return {"ok": True, "msg": "Marcado como postado ✓" if status == "postado" else "Pulado"}

    def limpar_fila(self):
        self.db._q("DELETE FROM loja_fila WHERE status='pendente'", commit=True)
        return {"ok": True, "msg": "Fila limpa"}

    def estado(self):
        hoje = _inicio_do_dia()
        n_hoje = self.db._q("SELECT COUNT(*) c FROM loja_fila WHERE status='postado' AND postado_ts>=?", (hoje,))[0]["c"]
        return {"fila": self.fila(), "postados_hoje": n_hoje}
