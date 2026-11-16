"""Mercado Livre: busca produtos pela API e monta o link de afiliado.

- Busca: precisa de um app grátis em developers.mercadolivre.com.br (Client ID + Secret).
- Link de afiliado: o ML não tem API pra isso. A gente pega os parâmetros do SEU link
  de afiliado (gerado no painel de afiliados) e cola em cada produto.
  Ex.: ML_AFILIADO_PARAMS=matt_word=seuusuario&matt_tool=12345678
"""
import json
import time
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import requests

from config import PASTA

API = "https://api.mercadolibre.com"
ARQ_TOKEN = PASTA / "ml_login.json"


def _f(v, padrao=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


class MercadoLivre:
    nome = "mercadolivre"

    def __init__(self, client_id: str, client_secret: str, afiliado_params: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.afiliado_params = dict(parse_qsl(afiliado_params.strip().lstrip("?")))
        self._token = None
        self._token_expira = 0
        self.diag = []

    @property
    def configurada(self) -> bool:
        return bool(self.client_id and self.client_secret and self.afiliado_params)

    # ---------- login com a conta do usuário (token de usuário) ----------
    REDIRECT = "https://www.google.com"

    def url_login(self) -> str:
        return ("https://auth.mercadolivre.com.br/authorization?response_type=code"
                f"&client_id={self.client_id}&redirect_uri={self.REDIRECT}")

    def _salvar_usuario(self, d: dict):
        info = {"access_token": d["access_token"], "refresh_token": d.get("refresh_token"),
                "expira": int(time.time()) + int(d.get("expires_in", 21600)), "user_id": d.get("user_id")}
        ARQ_TOKEN.write_text(json.dumps(info), encoding="utf-8")
        return info

    def _ler_usuario(self) -> dict | None:
        try:
            return json.loads(ARQ_TOKEN.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @property
    def conectado(self) -> bool:
        return bool(self._ler_usuario())

    def conectar(self, link_ou_codigo: str) -> dict:
        """Recebe o link do google.com?code=... (ou só o código) e troca pelo token."""
        txt = (link_ou_codigo or "").strip()
        codigo = dict(parse_qsl(urlparse(txt).query)).get("code") if "code=" in txt else txt
        if not codigo:
            raise RuntimeError("Não achei o código no link. Copie o endereço inteiro da página do Google.")
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "authorization_code", "client_id": self.client_id,
            "client_secret": self.client_secret, "code": codigo, "redirect_uri": self.REDIRECT,
        }, timeout=30)
        if not r.ok:
            raise RuntimeError(f"ML recusou o código ({r.status_code}): {r.text[:120]}. Gere um novo, ele vale poucos minutos.")
        info = self._salvar_usuario(r.json())
        self._token, self._token_expira = info["access_token"], info["expira"]
        return info

    def desconectar(self):
        ARQ_TOKEN.unlink(missing_ok=True)
        self._token, self._token_expira = None, 0

    def _token_app(self) -> str:
        if self._token and time.time() < self._token_expira - 60:
            return self._token
        u = self._ler_usuario()
        if u:  # usa a conta conectada; renova sozinho quando vence
            if time.time() < u["expira"] - 60:
                self._token, self._token_expira = u["access_token"], u["expira"]
                return self._token
            if u.get("refresh_token"):
                r = requests.post(f"{API}/oauth/token", data={
                    "grant_type": "refresh_token", "client_id": self.client_id,
                    "client_secret": self.client_secret, "refresh_token": u["refresh_token"],
                }, timeout=30)
                if r.ok:
                    info = self._salvar_usuario(r.json())
                    self._token, self._token_expira = info["access_token"], info["expira"]
                    return self._token
            raise RuntimeError("O login do Mercado Livre venceu. Vá em Config → Conectar Mercado Livre de novo.")
        r = requests.post(f"{API}/oauth/token", data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        self._token = d["access_token"]
        self._token_expira = time.time() + int(d.get("expires_in", 21600))
        return self._token

    def link_afiliado(self, url: str) -> str:
        p = urlparse(url)
        q = dict(parse_qsl(p.query))
        q.update(self.afiliado_params)
        return urlunparse(p._replace(query=urlencode(q)))

    # Desde 2026 o ML devolve 403 em /sites/MLB/search pra apps comuns.
    # Então a gente usa o catálogo (/products/search) e os mais vendidos da
    # categoria (/highlights), que continuam liberados com o token do app.
    def _get(self, caminho: str, **params):
        r = requests.get(f"{API}{caminho}", params=params,
                         headers={"Authorization": f"Bearer {self._token_app()}"}, timeout=30)
        nome = caminho.strip("/").split("/")[0] + ("/" + caminho.strip("/").split("/")[1] if caminho.count("/") > 1 and not caminho.startswith("/products/MLB") else "")
        if r.status_code in (401, 403, 404):
            try:
                motivo = (r.json().get("message") or r.json().get("error") or "")[:60]
            except ValueError:
                motivo = ""
            self.diag.append(f"{nome}={r.status_code}{(' ' + motivo) if motivo else ''}")
            return None
        self.diag.append(f"{nome}={r.status_code}")
        r.raise_for_status()
        return r.json()

    def _ids_catalogo(self, palavra: str, limite: int) -> list[str]:
        ids = []
        d = self._get("/products/search", status="active", site_id="MLB", q=palavra, limit=limite)
        for p in (d or {}).get("results", []):
            if p.get("id"):
                ids.append(p["id"])
        if ids:
            return ids
        # plano B: descobre a categoria da palavra e pega os mais vendidos dela
        cats = self._get("/sites/MLB/domain_discovery/search", q=palavra, limit=1) or []
        if cats and cats[0].get("category_id"):
            hl = self._get(f"/highlights/MLB/category/{cats[0]['category_id']}") or {}
            ids = [c["id"] for c in hl.get("content", []) if c.get("type") == "PRODUCT"]
        return ids[:limite]

    def _produto(self, pid: str) -> dict | None:
        p = self._get(f"/products/{pid}")
        if not p:
            return None
        bbw = p.get("buy_box_winner") or {}
        preco = _f(bbw.get("price"))
        if preco <= 0:
            return None  # sem vendedor ativo no momento
        original = _f(bbw.get("original_price"))
        desconto = round((1 - preco / original) * 100) if original > preco > 0 else 0
        fotos = p.get("pictures") or []
        imagem = (fotos[0].get("url") if fotos else "") or ""
        link = p.get("permalink") or f"https://www.mercadolivre.com.br/p/{pid}"
        return {
            "id": f"ml:{pid}",
            "plataforma": "mercadolivre",
            "nome": p.get("name", ""),
            "preco": preco,
            "desconto": desconto,
            "nota": 4.6,  # a API não entrega nota pra apps comuns
            "vendas": 0,
            "frete_gratis": bool((bbw.get("shipping") or {}).get("free_shipping")),
            "comissao_taxa": 0.0,
            "imagem": imagem.replace("http://", "https://"),
            "link": self.link_afiliado(link),
        }

    def buscar(self, palavra: str, limit: int = 12) -> list[dict]:
        self.diag = []
        ids = self._ids_catalogo(palavra, limit)
        if not ids:
            raise RuntimeError(f"nada pra \"{palavra}\" — respostas do ML: {', '.join(self.diag) or 'nenhuma'}")
        ofertas = []
        for pid in ids[:limit]:
            try:
                o = self._produto(pid)
            except requests.RequestException:
                continue
            if o:
                ofertas.append(o)
        return ofertas
