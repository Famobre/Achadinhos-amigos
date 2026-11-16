"""Configurações do painel.

- Chaves/senhas: podem vir do .env OU ser digitadas no próprio painel (aba Contas),
  que salva em segredos.json. O que estiver no painel tem prioridade.
- O resto (palavras, ritmo, filtros, canais) fica no config.json.
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# pasta onde ficam .env, config.json e dados.db (do lado do .exe quando empacotado)
PASTA = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
load_dotenv(PASTA / ".env")

ARQ_CONFIG = PASTA / "config.json"
ARQ_SEGREDOS = PASTA / "segredos.json"

# chave -> (rótulo no painel, grupo, é senha?)
CHAVES = {
    "SHOPEE_APP_ID": ("App ID (Open API)", "Shopee", False),
    "SHOPEE_SECRET": ("Secret (Open API)", "Shopee", True),
    "SHOPEE_AFFILIATE_ID": ("ID de afiliado (pro feed)", "Shopee", False),
    "SHOPEE_FEED_URL": ("Link do feed de produtos (CSV)", "Shopee", True),
    "ML_CLIENT_ID": ("Client ID", "Mercado Livre", False),
    "ML_CLIENT_SECRET": ("Client Secret", "Mercado Livre", True),
    "ML_AFILIADO_PARAMS": ("Tag de afiliado (matt_word=...&matt_tool=...)", "Mercado Livre", False),
    "ALI_APP_KEY": ("App Key", "AliExpress", False),
    "ALI_APP_SECRET": ("App Secret", "AliExpress", True),
    "ALI_TRACKING_ID": ("Tracking ID", "AliExpress", False),
    "AMZ_CREDENTIAL_ID": ("Credential ID (client_id)", "Amazon", False),
    "AMZ_CREDENTIAL_SECRET": ("Credential Secret", "Amazon", True),
    "AMZ_PARTNER_TAG": ("Partner tag (ex.: seutag-20)", "Amazon", False),
    "TELEGRAM_TOKEN": ("Token do bot", "Telegram", True),
    "TELEGRAM_CANAL": ("Canal principal (@canal)", "Telegram", False),
    "TELEGRAM_ADMIN": ("Seu chat id (avisos de erro)", "Telegram", False),
    "ANTHROPIC_API_KEY": ("API key", "IA (Claude)", True),
}


def _segredos() -> dict:
    try:
        return json.loads(ARQ_SEGREDOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cred(chave: str) -> str:
    return (_segredos().get(chave) or os.getenv(chave, "") or "").strip()


def salvar_segredos(novos: dict):
    atual = _segredos()
    for k, v in (novos or {}).items():
        if k not in CHAVES:
            continue
        v = (v or "").strip()
        if v == "••••••":   # campo de senha não alterado
            continue
        v = v.replace("•", "").strip()  # colou por cima dos pontinhos: fica só o que foi colado
        if v:
            atual[k] = v
        else:
            atual.pop(k, None)
    ARQ_SEGREDOS.write_text(json.dumps(atual, indent=2), encoding="utf-8")


def segredos_para_painel() -> list:
    """Lista de campos pro painel, sem mostrar senhas."""
    out = []
    for k, (rotulo, grupo, senha) in CHAVES.items():
        v = cred(k)
        out.append({"chave": k, "rotulo": rotulo, "grupo": grupo, "senha": senha,
                    "valor": ("••••••" if v else "") if senha else v, "preenchido": bool(v)})
    return out


# compatibilidade com o código antigo (lido na hora)
def __getattr__(nome):
    if nome in CHAVES:
        return cred(nome)
    raise AttributeError(nome)


PADRAO = {
    "plataformas": {"shopee": True, "mercadolivre": True, "aliexpress": True, "amazon": True},
    "palavras": [
        "fone bluetooth", "smartwatch", "carregador turbo", "luminaria led", "organizador",
        "garrafa termica", "mouse gamer", "kit maquiagem", "utensilios cozinha",
        "camiseta masculina", "tenis feminino", "caixa de som", "ring light", "suporte celular",
    ],
    # palavras em inglês pra AliExpress/Amazon fora do Brasil
    "palavras_global": [
        "wireless earbuds", "smart watch", "phone holder", "led strip lights", "mini projector",
        "gaming mouse", "portable blender", "car accessories", "kitchen gadgets", "makeup brush set",
    ],
    "aliexpress": {"moeda": "USD", "idioma": "EN", "pais": "US"},
    "amazon": {"marketplace": "www.amazon.com"},
    "intervalo_min": 40,
    "max_posts_dia": 25,
    "hora_inicio": 8,
    "hora_fim": 23,
    "nota_minima": 4.5,
    "vendas_minimas": 100,
    "preco_min": 3,
    "preco_max": 300,
    "ligar_ao_abrir": False,
    "manter_acordado": True,
    "usar_card": True,
    "estilo_imagem": "foto",  # "foto" = só a foto do produto | "card" = card com preço e cupom
    # texto extra no fim dos posts fora do Telegram (pra levar gente pro canal)
    "rodape": "",
    "link_whatsapp": "",  # link do seu canal do WhatsApp (vira botão no Telegram)
    "feed_shopee": {"desconto_min": 20, "preco_min": 5, "preco_max": 300, "nota_min": 4.6,
                    "categorias_excluir": []},
    "intervalo_sem_fila_min": 0,  # >0: intervalo usado quando a fila manual (ML) está vazia
    "manual_a_cada": 2,      # 1 oferta da fila manual (ML) a cada N posts; o resto vem do feed da Shopee
    "ritmo_pico": True,      # posta mais seguido no almoço e à noite
    "botoes_telegram": True, # botões "VER OFERTA" e "Mandar pra um amigo" embaixo dos posts
    "zap_a_cada": 6,             # manda pro seu privado 1 a cada N posts
    "whatsapp_assistido": True,  # cada post também vai pro seu privado com botão "POSTAR NO WHATSAPP"
    "top_semanal": True,
    "resumo_diario": True,   # 23h: resumo do dia no seu privado do Telegram     # sexta 20h: TOP 5 da semana no Telegram
    "selo_campanha": True,   # "ESQUENTA 11.11", "BLACK FRIDAY"... no topo dos posts nas datas
    "ia": {"ativa": True, "motor": "crewai", "modelo": "claude-haiku-5-5"},
    "mostrar_loja": False,   # aba "Loja" (fila de postagem em grupos do Facebook)
    # canais extras além do Telegram principal do .env/Contas
    "canais": [],
}


ARQ_ESTADO = PASTA / "estado.json"


def estado() -> dict:
    try:
        return json.loads(ARQ_ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def salvar_estado(**kv):
    e = estado()
    e.update(kv)
    ARQ_ESTADO.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar() -> dict:
    cfg = json.loads(json.dumps(PADRAO))
    if ARQ_CONFIG.exists():
        try:
            salvo = json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))
            for k, v in salvo.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except json.JSONDecodeError:
            pass
    return cfg


def salvar(novo: dict) -> dict:
    cfg = carregar()
    for k in PADRAO:
        if k in novo:
            if isinstance(novo[k], dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(novo[k])
            else:
                cfg[k] = novo[k]
    ARQ_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def status_credenciais() -> dict:
    c = cred
    return {
        "shopee": bool((c("SHOPEE_APP_ID") and c("SHOPEE_SECRET")) or c("SHOPEE_AFFILIATE_ID")),
        "mercadolivre": bool(c("ML_CLIENT_ID") and c("ML_CLIENT_SECRET") and c("ML_AFILIADO_PARAMS")),
        "aliexpress": bool(c("ALI_APP_KEY") and c("ALI_APP_SECRET") and c("ALI_TRACKING_ID")),
        "amazon": bool(c("AMZ_CREDENTIAL_ID") and c("AMZ_CREDENTIAL_SECRET") and c("AMZ_PARTNER_TAG")),
        "telegram": bool(c("TELEGRAM_TOKEN") and c("TELEGRAM_CANAL")),
        "ia": bool(c("ANTHROPIC_API_KEY")),
    }
