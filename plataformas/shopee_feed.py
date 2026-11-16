"""Feed de produtos da Shopee (painel de afiliados → Criativo → Feed de produto).

A Shopee publica todo dia um CSV com 100 mil produtos (preço, desconto, nota, foto…).
O painel lê o CSV, guarda as melhores ofertas num banco local e o bot posta delas.

O link curto do feed (shope.ee/an_redir?origin_link=...) vem SEM o seu código —
por isso a gente acrescenta &affiliate_id=SEU_ID (e um sub_id pra saber de onde veio o clique).
"""
import csv
import heapq
import math
import random
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

COLS_OBRIGATORIAS = {"itemid", "title", "sale_price", "image_link"}


def _f(v, padrao=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


def pontuar(desc, likes, nota, loja_nota) -> float:
    return desc / 8 + math.log10(likes + 1) * 2.2 + nota + loja_nota * 0.5


class FeedShopee:
    nome = "shopee"

    def __init__(self, db, affiliate_id: str = "", sub_id: str = "achadinhos"):
        self.db = db
        self.affiliate_id = (affiliate_id or "").strip()
        self.sub_id = sub_id
        with db.lock:
            db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS feed_shopee (
                itemid TEXT PRIMARY KEY, titulo TEXT, preco REAL, preco_antigo REAL, desconto REAL,
                nota REAL, likes INTEGER, loja TEXT, loja_nota REAL, categoria TEXT, imagem TEXT,
                link_produto TEXT, link_curto TEXT, pontos REAL, atualizado INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_feed_pontos ON feed_shopee(pontos DESC);
            """)
            db.conn.commit()

    # ---------------- status ----------------
    @property
    def configurada(self) -> bool:
        return bool(self.affiliate_id) and self.total() > 0

    def total(self) -> int:
        return self.db._q("SELECT COUNT(*) c FROM feed_shopee")[0]["c"]

    def resumo(self) -> dict:
        r = self.db._q("SELECT COUNT(*) c, MAX(atualizado) a FROM feed_shopee")[0]
        cats = self.db._q("SELECT categoria, COUNT(*) n FROM feed_shopee GROUP BY categoria ORDER BY n DESC LIMIT 6")
        return {"total": r["c"], "atualizado": r["a"], "categorias": [dict(c) for c in cats],
                "affiliate_id": bool(self.affiliate_id)}

    # ---------------- importar ----------------
    def importar_linhas(self, linhas, filtro: dict, guardar: int = 3000, log=lambda *_: None) -> dict:
        """linhas: iterável de dicts do CSV. Mantém só as `guardar` melhores que passam no filtro."""
        dmin = filtro.get("desconto_min", 20)
        pmin, pmax = filtro.get("preco_min", 5), filtro.get("preco_max", 300)
        nmin = filtro.get("nota_min", 4.6)
        excluir = [c.lower() for c in filtro.get("categorias_excluir", [])]
        melhores, lidas = [], 0
        for row in linhas:
            lidas += 1
            preco = _f(row.get("sale_price"))
            antigo = _f(row.get("price"))
            desc = _f(row.get("discount_percentage"))
            nota = _f(row.get("item_rating"))
            if not (pmin <= preco <= pmax) or desc < dmin or nota < nmin:
                continue
            cat = row.get("global_category1") or ""
            if cat.lower() in excluir:
                continue
            likes = int(_f(row.get("like")))
            lnota = _f(row.get("shop_rating"))
            pts = pontuar(desc, likes, nota, lnota)
            item = (str(row.get("itemid")), (row.get("title") or "").strip()[:200], preco,
                    antigo if antigo > preco else None, desc, nota, likes, row.get("shop_name"), lnota, cat,
                    row.get("image_link") or row.get("image_link_3"), row.get("product_link"),
                    row.get("product_short link") or row.get("product_short_link"), pts)
            chave = (pts, lidas, item)  # heap com as N melhores (lidas desempata)
            if len(melhores) < guardar:
                heapq.heappush(melhores, chave)
            elif pts > melhores[0][0]:
                heapq.heapreplace(melhores, chave)
            if lidas % 20000 == 0:
                log(f"Feed Shopee: {lidas} produtos lidos…")
        agora = int(time.time())
        with self.db.lock:
            self.db.conn.execute("DELETE FROM feed_shopee")
            self.db.conn.executemany("INSERT OR REPLACE INTO feed_shopee VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     [(*m[2], agora) for m in melhores])
            self.db.conn.commit()
        return {"lidas": lidas, "guardadas": len(melhores)}

    def importar_arquivo(self, caminho: str, filtro: dict, log=lambda *_: None) -> dict:
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            leitor = csv.DictReader(f)
            faltam = COLS_OBRIGATORIAS - set(leitor.fieldnames or [])
            if faltam:
                raise RuntimeError(f"Esse arquivo não parece o feed da Shopee (faltam colunas: {', '.join(faltam)})")
            return self.importar_linhas(leitor, filtro, log=log)

    def baixar_e_importar(self, url: str, filtro: dict, pasta: Path, log=lambda *_: None) -> dict:
        """Lê o feed direto da internet, linha por linha — não grava os ~190 MB no disco
        (importante na hospedagem, onde o espaço é pouco)."""
        import io
        log("Baixando e lendo o feed da Shopee (uns 190 MB, pode levar alguns minutos)…")
        with requests.get(url, stream=True, timeout=120, headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            r.raw.decode_content = True
            r.raw.auto_close = False  # senão o urllib3 fecha antes do csv terminar de ler
            texto = io.TextIOWrapper(r.raw, encoding="utf-8-sig", errors="replace", newline="")
            leitor = csv.DictReader(texto)
            faltam = COLS_OBRIGATORIAS - set(leitor.fieldnames or [])
            if faltam:
                raise RuntimeError(f"Esse link não parece o feed da Shopee (faltam colunas: {', '.join(faltam)})")
            return self.importar_linhas(leitor, filtro, log=log)

    # ---------------- usar ----------------
    def link_afiliado(self, link_curto: str, link_produto: str) -> str:
        base = link_curto or ("https://shope.ee/an_redir?" + urlencode({"origin_link": link_produto}))
        extra = {"affiliate_id": self.affiliate_id}
        if self.sub_id:
            extra["sub_id"] = self.sub_id
        return base + ("&" if "?" in base else "?") + urlencode(extra)

    def _oferta(self, r) -> dict:
        return {
            "id": f"shopee:{r['itemid']}", "plataforma": "shopee", "nome": r["titulo"], "preco": r["preco"],
            "preco_antigo": r["preco_antigo"] or 0, "moeda": "BRL", "desconto": r["desconto"] or 0,
            "nota": r["nota"] or 0, "vendas": 0, "likes": r["likes"], "comissao_taxa": 0.0,
            "imagem": r["imagem"], "link": self.link_afiliado(r["link_curto"], r["link_produto"]),
            "_feed": True, "_categoria": r["categoria"] or "",
        }

    def buscar_variado(self, evitar_cats=(), limit: int = 30, pesos: dict | None = None) -> list[dict]:
        """Sorteia uma categoria (pesando pelo tamanho dela no feed, sem repetir as últimas postadas)
        e devolve as melhores ofertas dela. Deixa o canal variado: casa, beleza, moda, pet..."""
        cats = [(r["categoria"], r["n"]) for r in
                self.db._q("SELECT categoria, COUNT(*) n FROM feed_shopee GROUP BY categoria")]
        pesos = pesos or {}
        # peso 0 = categoria nunca sai (ex.: comida); sem peso definido = 1
        cats = [(c, n) for c, n in cats if float(pesos.get(c, 1)) > 0]
        cats = [(c, n) for c, n in cats if c not in set(evitar_cats)] or cats
        if not cats:
            return []
        cat = random.choices([c for c, _ in cats], weights=[(n ** 0.5) * float(pesos.get(c, 1)) for c, n in cats])[0]
        rows = self.db._q("SELECT * FROM feed_shopee WHERE categoria=? ORDER BY pontos DESC LIMIT ?", (cat, limit))
        return [self._oferta(r) for r in rows]

    def buscar(self, palavra: str, limit: int = 30) -> list[dict]:
        termos = [t for t in (palavra or "").lower().split() if len(t) > 2]
        if termos:
            onde = " AND ".join("lower(titulo) LIKE ?" for _ in termos)
            rows = self.db._q(f"SELECT * FROM feed_shopee WHERE {onde} ORDER BY pontos DESC LIMIT ?",
                              (*[f"%{t}%" for t in termos], limit))
            if rows:
                return [self._oferta(r) for r in rows]
        # nada com a palavra: pega um sorteio entre as 200 melhores
        rows = self.db._q("SELECT * FROM feed_shopee ORDER BY pontos DESC LIMIT 200")
        rows = random.sample(list(rows), min(limit, len(rows)))
        return [self._oferta(r) for r in rows]
