"""Decide quais ofertas valem a pena postar."""
import math


def passa_filtro(o: dict, cfg: dict) -> bool:
    if not o.get("link") or o["preco"] <= 0:
        return False
    if not (cfg["preco_min"] <= o["preco"] <= cfg["preco_max"]):
        return False
    if o.get("_feed"):
        return True  # o feed já foi filtrado por desconto/nota na importação
    if o["plataforma"] in ("shopee", "aliexpress"):
        # essas APIs trazem nota e vendas de verdade
        return o["nota"] >= cfg["nota_minima"] - (0.2 if o["plataforma"] == "aliexpress" else 0) \
            and o["vendas"] >= cfg["vendas_minimas"]
    # Mercado Livre e Amazon: a API não traz vendas, então exige desconto real ou frete grátis
    return o.get("desconto", 0) >= 10 or o.get("frete_gratis", False)


def pontuar(o: dict) -> float:
    vendas = math.log10((o.get("vendas") or o.get("likes") or 0) + 1) * 2   # 1000 vendas -> 6
    desconto = o.get("desconto", 0) / 10                  # 50% -> 5
    nota = o.get("nota", 0)
    comissao = o.get("comissao_taxa", 0) * 50             # 8% -> 4
    frete = 2 if o.get("frete_gratis") else 0
    # faixa de compra por impulso (o que mais sai em canal de achadinhos): R$10–80
    p = o.get("preco", 0) if o.get("moeda", "BRL") == "BRL" else 0
    faixa = 2 if 10 <= p <= 80 else (-1.5 if p > 150 else 0) if p else 0
    return vendas + desconto + nota + comissao + frete + faixa


def melhores(ofertas: list[dict], cfg: dict) -> list[dict]:
    return sorted((o for o in ofertas if passa_filtro(o, cfg)), key=pontuar, reverse=True)
