"""IA do bot: escolhe a melhor oferta entre as candidatas e escreve o post em vários idiomas.

Motor 1 — CrewAI (crewAIInc/crewAI) com Claude: dois agentes
  • Curador: escolhe qual produto tem mais chance de vender
  • Redator: escreve a chamada e a descrição em cada idioma pedido
Motor 2 — Claude direto (plano B): mesma tarefa numa chamada só.
Sem chave de IA, o bot usa a primeira candidata e textos prontos.
"""
import json
import re

import requests

import config

IDIOMAS = {"pt": "português do Brasil", "en": "English", "es": "español"}


def _json(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt or "", re.S)
    return json.loads(m.group(0)) if m else {}


def _resumo(cands: list[dict]) -> str:
    linhas = []
    for i, o in enumerate(cands):
        linhas.append(json.dumps({
            "n": i, "produto": o["nome"][:120], "loja": o["plataforma"], "preco": f"{o['preco']:.2f} {o.get('moeda', 'BRL')}",
            "desconto_%": o.get("desconto", 0), "nota": round(o.get("nota", 0), 1), "vendidos": o.get("vendas", 0),
            "frete_gratis": o.get("frete_gratis", False),
        }, ensure_ascii=False))
    return "\n".join(linhas)


def _instrucoes_redator(idiomas: list[str]) -> str:
    nomes = ", ".join(f'"{i}" ({IDIOMAS[i]})' for i in idiomas)
    return (
        f"Escreva o post para os idiomas: {nomes}. Para cada idioma: 'chamada' (gancho de até 10 palavras, "
        "1 emoji, sem preço) e 'descricao' (1 ou 2 frases curtas dizendo por que vale a pena, tom de amigo "
        "indicando achado, sem exagero e sem prometer o que o produto não faz, sem hashtags). "
        'Responda SÓ com JSON: {"escolhido": n, "textos": {"pt": {"chamada": "...", "descricao": "..."}}}'
    )


def _crewai(cands, idiomas, chave, modelo) -> dict:
    from crewai import LLM, Agent, Crew, Process, Task  # import pesado, só quando usar

    llm = LLM(model=f"anthropic/{modelo}", api_key=chave, temperature=0.7)
    curador = Agent(
        role="Curador de ofertas",
        goal="Escolher o produto com mais chance de gerar vendas num canal de promoções",
        backstory="Você analisa ofertas de marketplaces há anos e sabe o que o público compra por impulso: "
                  "bom desconto, preço baixo, muitas vendas e boa avaliação.",
        llm=llm, verbose=False, allow_delegation=False,
    )
    redator = Agent(
        role="Redator de posts de ofertas",
        goal="Escrever posts curtos e chamativos que fazem a pessoa clicar",
        backstory="Copywriter de canais de achadinhos no Telegram e Instagram, escreve natural, sem cara de anúncio.",
        llm=llm, verbose=False, allow_delegation=False,
    )
    t1 = Task(
        description="Candidatas (uma por linha, em JSON):\n" + _resumo(cands) +
                    '\n\nEscolha UMA. Responda só JSON: {"escolhido": n, "motivo": "..."}',
        expected_output='JSON {"escolhido": n, "motivo": "..."}', agent=curador,
    )
    t2 = Task(
        description="Use o produto escolhido pelo curador (lista de candidatas acima):\n" + _resumo(cands) +
                    "\n\n" + _instrucoes_redator(idiomas),
        expected_output='JSON {"escolhido": n, "textos": {...}}', agent=redator, context=[t1],
    )
    res = Crew(agents=[curador, redator], tasks=[t1, t2], process=Process.sequential, verbose=False).kickoff()
    return _json(getattr(res, "raw", str(res)))


def _direto(cands, idiomas, chave, modelo) -> dict:
    corpo = {"model": modelo, "max_tokens": 1500, "thinking": {"type": "disabled"},
             "messages": [{"role": "user", "content":
              "Você é curador e redator de um canal de achadinhos (público brasileiro, maioria compra pelo celular). "
              "Candidatas (JSON por linha):\n" + _resumo(cands) +
              "\n\n1) Escolha a que tem mais chance de vender: produto útil do dia a dia ou que dá vontade na hora, "
              "preço baixo, boa avaliação, desconto real.\n2) " + _instrucoes_redator(idiomas)}]}
    url = "https://api.anthropic.com/v1/messages"
    cab = {"x-api-key": chave, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = requests.post(url, headers=cab, json=corpo, timeout=60)
    if r.status_code == 400 and "thinking" in r.text:  # modelo que não aceita o campo: tenta sem
        corpo.pop("thinking")
        r = requests.post(url, headers=cab, json=corpo, timeout=60)
    if not r.ok:
        raise RuntimeError(f"API respondeu {r.status_code}: {r.text[:150]}")
    blocos = r.json().get("content") or []
    texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text")
    if not texto:
        raise RuntimeError(f"resposta sem texto: {str(blocos)[:150]}")
    return _json(texto)


_SEM_CREW = []


def crewai_instalado() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("crewai") is not None
    except Exception:
        return False


def escolher_e_escrever(cands: list[dict], idiomas: list[str], log=lambda *_: None) -> tuple[dict, dict]:
    """Retorna (oferta escolhida, {idioma: {chamada, descricao}}). Textos vazios = usar template."""
    cfg = config.carregar()["ia"]
    chave = config.cred("ANTHROPIC_API_KEY")
    idiomas = [i for i in dict.fromkeys(idiomas) if i in IDIOMAS] or ["pt"]
    if not (cfg.get("ativa") and chave and cands):
        return cands[0], {}
    cands = cands[:8]
    modelo = cfg.get("modelo") or "claude-haiku-5-5"
    tentativas = []
    if cfg.get("motor") == "crewai" and crewai_instalado() and not _SEM_CREW:
        tentativas.append(("CrewAI", _crewai))
    tentativas.append(("Claude", _direto))
    for nome, fn in tentativas:
        try:
            d = fn(cands, idiomas, chave, modelo)
            n = int(d.get("escolhido", 0))
            escolhida = cands[n] if 0 <= n < len(cands) else cands[0]
            textos = {k: v for k, v in (d.get("textos") or {}).items() if isinstance(v, dict)}
            log(f"IA ({nome}) escolheu: {escolhida['nome'][:50]}")
            return escolhida, textos
        except Exception as e:
            if nome == "CrewAI" and "not available" in str(e):
                _SEM_CREW.append(1)  # falta o crewai[anthropic]: para de tentar até reabrir o painel
                log("IA: CrewAI sem o pacote do Claude — usando Claude direto (instale em Contas → IA)")
                continue
            log(f"IA ({nome}) falhou: {str(e)[:120]}")
    return cands[0], {}
