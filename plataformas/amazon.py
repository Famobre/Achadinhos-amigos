"""Amazon Creators API (substituiu a PA-API 5, que foi desligada).

Cadastro: vira Amazon Associate no país desejado → registra na Creators API →
pega Credential ID e Secret. O token é OAuth (client_credentials) no Login with Amazon.
Docs: https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction
"""
import time

import requests

API = "https://creatorsapi.amazon/catalog/v1"

# servidor de token por região (Login with Amazon)
TOKEN_POR_REGIAO = {
    "na": "https://api.amazon.com/auth/o2/token",
    "eu": "https://api.amazon.co.uk/auth/o2/token",
    "fe": "https://api.amazon.co.jp/auth/o2/token",
}
REGIAO = {
    "www.amazon.com": "na", "www.amazon.ca": "na", "www.amazon.com.mx": "na", "www.amazon.com.br": "na",
    "www.amazon.co.uk": "eu", "www.amazon.de": "eu", "www.amazon.fr": "eu", "www.amazon.it": "eu",
    "www.amazon.es": "eu", "www.amazon.nl": "eu", "www.amazon.se": "eu", "www.amazon.pl": "eu",
    "www.amazon.com.be": "eu", "www.amazon.ie": "eu", "www.amazon.com.tr": "eu", "www.amazon.ae": "eu",
    "www.amazon.sa": "eu", "www.amazon.eg": "eu", "www.amazon.in": "eu",
    "www.amazon.co.jp": "fe", "www.amazon.sg": "fe", "www.amazon.com.au": "fe",
}
MOEDA = {
    "www.amazon.com": "USD", "www.amazon.ca": "CAD", "www.amazon.com.mx": "MXN", "www.amazon.com.br": "BRL",
    "www.amazon.co.uk": "GBP", "www.amazon.de": "EUR", "www.amazon.fr": "EUR", "www.amazon.it": "EUR",
    "www.amazon.es": "EUR", "www.amazon.nl": "EUR", "www.amazon.ie": "EUR", "www.amazon.com.be": "EUR",
    "www.amazon.co.jp": "JPY", "www.amazon.com.au": "AUD", "www.amazon.in": "INR",
}


def _f(v, padrao=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao


class Amazon:
    nome = "amazon"

    def __init__(self, credential_id: str, credential_secret: str, partner_tag: str,
                 marketplace: str = "www.amazon.com"):
        self.cid, self.secret, self.tag = credential_id, credential_secret, partner_tag
        self.marketplace = marketplace
        self._token, self._expira = None, 0

    @property
    def configurada(self) -> bool:
        return bool(self.cid and self.secret and self.tag)

    def _token_app(self) -> str:
        if self._token and time.time() < self._expira - 60:
            return self._token
        url = TOKEN_POR_REGIAO[REGIAO.get(self.marketplace, "na")]
        r = requests.post(url, json={
            "grant_type": "client_credentials", "client_id": self.cid,
            "client_secret": self.secret, "scope": "creatorsapi::default",
        }, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Amazon recusou as credenciais ({r.status_code}): {r.text[:150]}")
        d = r.json()
        self._token, self._expira = d["access_token"], time.time() + int(d.get("expires_in", 3600))
        return self._token

    def buscar(self, palavra: str, limit: int = 10) -> list[dict]:
        corpo = {
            "partnerTag": self.tag, "marketplace": self.marketplace, "keywords": palavra,
            "itemCount": min(limit, 10), "availability": "Available", "minSavingPercent": 5,
            "resources": ["images.primary.large", "itemInfo.title", "offersV2.listings.price",
                          "offersV2.listings.availability", "customerReviews.starRating"],
        }
        r = requests.post(f"{API}/searchItems", json=corpo, timeout=30, headers={
            "Authorization": f"Bearer {self._token_app()}", "Content-Type": "application/json",
            "x-marketplace": self.marketplace,
        })
        if r.status_code in (401, 403):
            raise RuntimeError(f"Amazon negou a busca ({r.status_code}). A conta precisa estar aprovada "
                               f"na Creators API pra esse país: {r.text[:120]}")
        r.raise_for_status()
        itens = ((r.json().get("searchResult") or {}).get("items")) or []
        ofertas = []
        for it in itens:
            listings = ((it.get("offersV2") or {}).get("listings")) or []
            if not listings:
                continue
            preco_info = (listings[0].get("price") or {})
            money = preco_info.get("money") or {}
            preco = _f(money.get("amount"))
            if preco <= 0:
                continue
            img = ((it.get("images") or {}).get("primary") or {})
            img = (img.get("large") or img.get("medium") or {}).get("url")
            estrelas = _f((((it.get("customerReviews") or {}).get("starRating")) or {}).get("value"))
            ofertas.append({
                "id": f"amz:{self.marketplace}:{it.get('asin')}",
                "plataforma": "amazon",
                "nome": (((it.get("itemInfo") or {}).get("title")) or {}).get("displayValue", ""),
                "preco": preco,
                "moeda": money.get("currency") or MOEDA.get(self.marketplace, "USD"),
                "desconto": _f((preco_info.get("savings") or {}).get("percentage")),
                "nota": estrelas,  # 0 = não mostra nota no post
                "vendas": 0,
                "comissao_taxa": 0.0,
                "imagem": img,
                "link": it.get("detailPageURL"),  # já vem com a sua partnerTag
                "pais": self.marketplace.replace("www.amazon.", ""),
            })
        return ofertas
