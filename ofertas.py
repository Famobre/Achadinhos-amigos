"""Ofertas manuais: você cola o link de afiliado (gerado no painel do ML, Shopee, etc.),
preenche nome/preço/foto e o bot posta com card em todos os canais.
Elas têm prioridade sobre as ofertas que o bot busca sozinho pelas APIs."""
import shutil
import time
import uuid
from pathlib import Path

from config import PASTA

PASTA_FOTOS = PASTA / "fotos_ofertas"


def _f(v, padrao=0.0):
    try:
        return float(str(v).replace("R$", "").replace("US$", "").replace(".", "").replace(",", ".")
                     if isinstance(v, str) and "," in v else v)
    except (TypeError, ValueError):
        return padrao


class Ofertas:
    def __init__(self, db):
        self.db = db
        PASTA_FOTOS.mkdir(exist_ok=True)
        with db.lock:
            db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ofertas_manuais (
                id INTEGER PRIMARY KEY AUTOINCREMENT, plataforma TEXT, nome TEXT, preco REAL,
                preco_antigo REAL, moeda TEXT, link TEXT, imagem TEXT, nota REAL, vendas INTEGER,
                frete_gratis INTEGER, status TEXT DEFAULT 'fila', criado INTEGER, postado_em INTEGER
            );""")
            cols = {r[1] for r in db.conn.execute("PRAGMA table_info(ofertas_manuais)")}
            for col in ("cupom", "condicao"):
                if col not in cols:
                    db.conn.execute(f"ALTER TABLE ofertas_manuais ADD COLUMN {col} TEXT")
            db.conn.commit()

    # ---------- helpers ----------
    @staticmethod
    def _uri(img: str | None) -> str | None:
        if not img:
            return None
        return img if img.startswith("http") else (PASTA_FOTOS / img).as_uri()

    def _como_oferta(self, r: dict) -> dict:
        img = r["imagem"]
        return {
            "id": f"man:{r['id']}", "_manual_id": r["id"], "plataforma": r["plataforma"] or "outra",
            "nome": r["nome"], "preco": r["preco"], "preco_antigo": r["preco_antigo"] or 0,
            "moeda": r["moeda"] or "BRL", "link": r["link"], "nota": r["nota"] or 0, "vendas": r["vendas"] or 0,
            "frete_gratis": bool(r["frete_gratis"]),
            "cupom": r.get("cupom") or "", "condicao": r.get("condicao") or "",
            "desconto": round((1 - r["preco"] / r["preco_antigo"]) * 100) if (r["preco_antigo"] or 0) > r["preco"] > 0 else 0,
            "imagem": img if (img or "").startswith("http") else (str(PASTA_FOTOS / img) if img else None),
        }

    # ---------- CRUD ----------
    def listar(self):
        out = []
        for r in self.db._q("SELECT * FROM ofertas_manuais ORDER BY status='postado', criado DESC LIMIT 100"):
            d = dict(r)
            d["imagem_uri"] = self._uri(d["imagem"])
            out.append(d)
        return out

    def importar_foto(self, caminho: str) -> dict | None:
        p = Path(caminho)
        if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return None
        novo = f"{uuid.uuid4().hex[:8]}{p.suffix.lower()}"
        shutil.copy2(p, PASTA_FOTOS / novo)
        return {"nome": novo, "uri": (PASTA_FOTOS / novo).as_uri()}

    def salvar(self, d: dict, oid: int | None = None) -> dict:
        nome, link = (d.get("nome") or "").strip(), (d.get("link") or "").strip()
        preco = _f(d.get("preco"))
        if not (nome and link.startswith("http") and preco > 0):
            return {"ok": False, "msg": "Preencha nome, link (https://...) e preço"}
        campos = (d.get("plataforma") or "outra", nome, preco, _f(d.get("preco_antigo")) or None,
                  d.get("moeda") or "BRL", link, (d.get("imagem") or "").strip() or None,
                  _f(d.get("nota")) or None, int(_f(d.get("vendas"))) or None, 1 if d.get("frete_gratis") else 0,
                  (d.get("cupom") or "").strip().upper() or None, (d.get("condicao") or "").strip() or None)
        if oid:
            self.db._q("""UPDATE ofertas_manuais SET plataforma=?, nome=?, preco=?, preco_antigo=?, moeda=?, link=?,
                          imagem=?, nota=?, vendas=?, frete_gratis=?, cupom=?, condicao=? WHERE id=?""", (*campos, oid), commit=True)
        else:
            self.db._q("""INSERT INTO ofertas_manuais (plataforma, nome, preco, preco_antigo, moeda, link, imagem, nota,
                          vendas, frete_gratis, cupom, condicao, criado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (*campos, int(time.time())), commit=True)
        return {"ok": True, "msg": "Oferta na fila ✓"}

    def apagar(self, oid: int):
        self.db._q("DELETE FROM ofertas_manuais WHERE id=?", (oid,), commit=True)
        return {"ok": True, "msg": "Oferta removida"}

    def repostar(self, oid: int):
        self.db._q("UPDATE ofertas_manuais SET status='fila' WHERE id=?", (oid,), commit=True)
        return {"ok": True, "msg": "Voltou pra fila"}

    # ---------- usadas pelo bot ----------
    def proxima(self, dias_validade: int = 5) -> dict | None:
        # oferta parada há muito tempo na fila provavelmente mudou de preço: não posta
        limite = int(time.time()) - dias_validade * 86400
        self.db._q("UPDATE ofertas_manuais SET status='vencida' WHERE status='fila' AND criado<?", (limite,), commit=True)
        r = self.db._q("SELECT * FROM ofertas_manuais WHERE status='fila' ORDER BY criado LIMIT 1")
        return self._como_oferta(dict(r[0])) if r else None

    def pegar(self, oid: int) -> dict | None:
        r = self.db._q("SELECT * FROM ofertas_manuais WHERE id=?", (oid,))
        return self._como_oferta(dict(r[0])) if r else None

    def marcar_postada(self, oid: int):
        self.db._q("UPDATE ofertas_manuais SET status='postado', postado_em=? WHERE id=?",
                   (int(time.time()), oid), commit=True)

    def na_fila(self) -> int:
        return self.db._q("SELECT COUNT(*) c FROM ofertas_manuais WHERE status='fila'")[0]["c"]


    # ---------- caixa de entrada: ofertas separadas pelo Claude (ou outro programa) ----------
    def importar_caixa(self, pasta: Path, log=lambda *_: None) -> int:
        """Lê ofertas_entrada*.json da pasta do painel, põe na fila e apaga o arquivo."""
        import json
        total = 0
        for arq in sorted(pasta.glob("ofertas_entrada*.json")):
            try:
                itens = json.loads(arq.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                log(f"Caixa de entrada: não consegui ler {arq.name} ({e})", "erro")
                continue
            existentes = {r["link"] for r in self.db._q("SELECT link FROM ofertas_manuais")}
            for d in itens if isinstance(itens, list) else [itens]:
                if d.get("link") in existentes:
                    continue
                if self.salvar(d).get("ok"):
                    total += 1
            try:
                arq.unlink()
            except OSError:
                pass
        if total:
            log(f"{total} ofertas novas chegaram na fila (caixa de entrada)")
        return total
