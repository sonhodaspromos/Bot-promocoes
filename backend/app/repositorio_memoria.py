"""Repositório em memória: usado nos testes e no comando `simular` (sem banco, sem internet).

Reproduz as regras essenciais do banco (sem duplicidade, trava de link validado, demo isolado,
pausa por preço) para que os testes dos serviços sejam realistas. As regras completas e
definitivas estão no PostgreSQL (supabase/migrations) e têm testes próprios.
"""
from __future__ import annotations

import itertools
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .erros import RegraViolada
from .modelos import CampanhaEncontrada, Cupom, ItemFila, OfertaEncontrada, ResultadoValidacao
from .repositorio import Repositorio, agora

CONFIG_PADRAO = {
    "publicacao.aprovacao_manual": False, "publicacao.horario_inicio": 0, "publicacao.horario_fim": 24,
    "publicacao.intervalo_minutos": 10, "publicacao.por_rodada": 2, "publicacao.max_por_dia": 60,
    "publicacao.feed_horario": 19, "filtros.desconto_minimo": 20, "filtros.nota_minima": 4.5,
    "filtros.repetir_apos_dias": 30, "monitor.ativo": True, "monitor.tolerancia_preco_pct": 5,
    "monitor.acompanhar_dias": 7, "monitor.verificar_links": True,
    "monitor.ler_preco_pagina": True, "monitor.intervalo_pagina_horas": 6, "transparencia.marcar_publi": True,
    "transparencia.aviso_afiliado": "Link de afiliado: posso ganhar comissão, sem custo extra para você.",
    "textos.chamada_instagram": "🔗 Link no nosso canal do Telegram (link na bio)",
    "textos.rodape_story": "Link no canal do Telegram", "textos.hashtags": "#achadinhos #promoção",
    "visual.nome_perfil": "Sonho das Promos", "visual.cor_principal": "#EE4D2D", "visual.cor_destaque": "#FFD400",
    "sistema.fuso_horario": "America/Recife", "logs.retencao_dias": 30,
    "cupons.ativo": True, "cupons.max_stories_dia": 3, "cupons.publicar_telegram": True, "cupons.horario": 9,
    "cupons.buscar_campanhas": True, "cupons.sugerir_produtos": True, "cupons.max_sugestoes": 10,
}


def _dominio_ok(url: str, dominios: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in dominios)


from .repositorio import CAMPOS_OPCIONAIS_PRODUTO, dados_de_ranking  # noqa: E402
from .requisitos import COLUNAS_PUBLICACAO, CONFIGS_PUBLICACAO, RPCS_PUBLICACAO  # noqa: E402


class MemoriaRepositorio(Repositorio):
    def __init__(self):
        self._ids = itertools.count(1)
        self.config = dict(CONFIG_PADRAO)
        # espelho de public.esquema_publicacao() de um banco com a migration 018
        self.esquema = {"versao": 18, "rpcs": sorted(RPCS_PUBLICACAO), "configuracoes": sorted(CONFIGS_PUBLICACAO),
                        "colunas": sorted(COLUNAS_PUBLICACAO)}
        self.t = {k: [] for k in ("marketplaces", "canais", "categorias", "produtos", "promocoes", "links",
                                  "fila", "historico", "comissoes", "execucoes", "logs", "precos", "cupons", "cupons_produtos", "cupons_pub",
                                  "comissoes_importacoes", "selecoes", "equivalentes", "experimentos", "resumos",
                                  "cliques", "comissoes_eventos", "canais_categorias", "tarefas", "alertas",
                                  "alertas_avisos", "wa_inscritos", "wa_eventos", "wa_envios", "orcamento", "wa_webhook",
                                  "ia_prompts", "ia_chamadas", "ia_conteudos", "orcamento_historico")}
        # prompt inicial do motor de IA (espelho resumido da migration 028)
        self.t["ia_prompts"].append({"id": 9001, "chave": "copy_oferta", "versao": 1, "ativo": True,
                                     "sistema": "Escreva textos de divulgação. Tom: {tom_de_voz}. Use só os dados. Responda em JSON.",
                                     "modelo": "Escreva a chamada e o texto.\n<dados>\n{dados}\n</dados>"})
        self.cofre: dict[tuple[str, str], str] = {}
        self.relogio = agora  # os testes trocam para simular a passagem do tempo
        for codigo, nome, doms, modo, padrao in [
                ("shopee", "Shopee", ["s.shopee.com.br", "shope.ee"], "demo", r"^https://(s\.shopee\.com\.br|shope\.ee)/[A-Za-z0-9_-]+"),
                ("mercado_livre", "Mercado Livre", ["meli.la", "mercadolivre.com"], "manual",
                 r"^https://(meli\.la/[A-Za-z0-9]+|mercadolivre\.com/sec/[A-Za-z0-9]+)"),
                ("demo", "Loja de Demonstração", ["demo.invalid"], "demo", r"^https://demo\.invalid/"),
                ("aliexpress", "AliExpress", ["s.click.aliexpress.com", "pt.aliexpress.com", "aliexpress.com"], "desativado", None),
                ("amazon", "Amazon", ["amzn.to", "www.amazon.com.br", "amazon.com.br"], "desativado", None),
                ("magalu", "Magalu", [], "manual", None)]:
            destino = {"shopee": ["shopee.com.br"], "mercado_livre": ["mercadolivre.com.br", "mercadolivre.com"]}.get(codigo, [])
            self.t["marketplaces"].append({"id": self._id(), "codigo": codigo, "nome": nome, "padrao_afiliado": padrao,
                                           "dominios_afiliado": doms, "modo": modo, "ativo": True, "dominios_destino": destino,
                                           "parametros_afiliado": [], "id_afiliado": None})
        for codigo, tipo, limite in [("telegram", "telegram", 200), ("instagram_story", "instagram_story", 45),
                                     ("instagram_feed", "instagram_feed", 1)]:
            self.t["canais"].append({"id": self._id(), "codigo": codigo, "tipo": tipo, "nome": codigo,
                                     "modo": "demo", "ativo": True, "limite_diario": limite})
        for slug, nome, palavras in [("eletronicos", "Eletrônicos", ["fone bluetooth", "smartwatch"]),
                                     ("casa", "Casa", ["air fryer", "organizador"]),
                                     ("beleza", "Beleza", ["maquiagem"])]:
            self.t["categorias"].append({"id": self._id(), "slug": slug, "nome": nome,
                                         "palavras_chave": palavras, "ativa": True})

    def _id(self):
        return next(self._ids)

    def _um(self, tabela, **filtro):
        return next((r for r in self.t[tabela] if all(r.get(k) == v for k, v in filtro.items())), None)

    # ---------------------------------------------------------------- cadastro
    def configuracoes(self): return dict(self.config)
    def marketplaces(self): return [dict(m) for m in self.t["marketplaces"]]
    def canais(self): return [dict(c) for c in self.t["canais"]]
    def categorias(self): return [dict(c) for c in self.t["categorias"]]

    def definir_modo(self, tabela, codigo, modo):
        alvo = self._um("marketplaces" if tabela == "marketplaces" else "canais", codigo=codigo)
        if alvo:
            alvo["modo"] = modo

    # ---------------------------------------------------------------- catálogo
    def salvar_produto(self, oferta, marketplace_id, categoria_id):
        mkt = self._um("marketplaces", id=marketplace_id)
        p = self._um("produtos", marketplace_id=marketplace_id, id_externo=oferta.id_externo)
        if p:
            self.atualizar_produto(p["id"], oferta.preco, oferta.disponivel, oferta.preco_original)
            p.update(titulo=oferta.titulo, imagem_url=oferta.imagem_url, preco_maximo_variacao=oferta.preco_maximo,
                     referencia_derivada=oferta.referencia_derivada)
            p.update({c: getattr(oferta, c) for c in CAMPOS_OPCIONAIS_PRODUTO if getattr(oferta, c, None) is not None})
            return p["id"], False
        p = {"id": self._id(), "marketplace_id": marketplace_id, "id_externo": oferta.id_externo,
             "categoria_id": categoria_id, "titulo": oferta.titulo, "preco_atual": oferta.preco,
             "preco_original": oferta.preco_original, "imagem_url": oferta.imagem_url, "disponivel": oferta.disponivel,
             "demo": oferta.demo or mkt["modo"] == "demo", "verificado_em": self.relogio(),
             "nota": oferta.nota, "total_vendas": oferta.total_vendas,
             "preco_maximo_variacao": oferta.preco_maximo, "referencia_derivada": oferta.referencia_derivada}
        p.update({c: getattr(oferta, c, None) for c in CAMPOS_OPCIONAIS_PRODUTO})
        self.t["produtos"].append(p)
        self._registrar_preco(p)
        return p["id"], True

    def _registrar_preco(self, p):
        self.t["precos"].append({"produto_id": p["id"], "preco": p["preco_atual"], "disponivel": p["disponivel"],
                                 "quando": self.relogio()})

    def _invalidar_analise(self, produto_id):
        for pr in self.t["promocoes"]:
            if pr["produto_id"] == produto_id and pr["status"] in ("encontrada", "aprovada", "publicada", "pausada"):
                pr.update(pontuacao=None, classificacao=None, confianca=None, analise=None, analisada_em=None)

    def criar_promocao(self, produto_id, oferta, status, origem):
        if any(pr["produto_id"] == produto_id and pr["status"] in ("encontrada", "aprovada", "publicada")
               for pr in self.t["promocoes"]):
            return None
        prod = self._um("produtos", id=produto_id)
        pr = {"id": self._id(), "produto_id": produto_id, "preco_promocional": oferta.preco,
              "preco_referencia": oferta.preco_original, "desconto_pct": oferta.desconto_pct, "status": status,
              "origem": origem, "demo": prod["demo"] or origem == "demo", "motivo_status": None,
              "publicada_em": None, "pausada_em": None, "criado_em": self.relogio(), "comissao_pct": oferta.comissao_pct,
              "pontuacao": None, "classificacao": None, "confianca": None, "analise": None, "analisada_em": None,
              "referencia_origem": ("loja" if oferta.preco_original else None), "preco_no_pix": False}
        self.t["promocoes"].append(pr)
        return pr["id"]

    def promocao_ativa(self, produto_id):
        pr = next((p for p in self.t["promocoes"] if p["produto_id"] == produto_id
                   and p["status"] in ("encontrada", "aprovada", "publicada")), None)
        return dict(pr) if pr else None

    def expirar_promocao(self, promocao_id, motivo):
        pr = self._um("promocoes", id=promocao_id)
        pr.update(status="expirada", motivo_status=motivo)
        for f in self.t["fila"]:
            if f["promocao_id"] == promocao_id and f["status"] in ("pendente", "processando"):
                f["status"] = "cancelada"

    def salvar_link(self, produto_id, promocao_id, url):
        l = self._um("links", url=url)
        if l:
            return l["id"]
        l = {"id": self._id(), "produto_id": produto_id, "promocao_id": promocao_id, "url": url,
             "validado": False, "ativo": True, "status_http": None, "motivo_invalido": None}
        self.t["links"].append(l)
        return l["id"]

    def _mkt_do_produto(self, produto_id):
        return self._um("marketplaces", id=self._um("produtos", id=produto_id)["marketplace_id"])

    def links_pendentes(self, limite, revalidar_atribuicao_horas=None):
        saida = []
        for l in self.t["links"]:
            if not l["ativo"]:
                continue
            nunca = not l["validado"] and l["status_http"] is None
            vencida = False
            if revalidar_atribuicao_horas and l["validado"]:
                quando = l.get("atribuicao_verificada_em")
                vencida = quando is None or self.relogio() - quando > timedelta(hours=float(revalidar_atribuicao_horas))
            if not (nunca or vencida):
                continue
            if not any(pr["produto_id"] == l["produto_id"] and pr["status"] in ("aprovada", "publicada")
                       for pr in self.t["promocoes"]):
                continue
            mkt = self._mkt_do_produto(l["produto_id"])
            saida.append({"link_id": l["id"], "url": l.get("url_curta") or l["url"], "produto_id": l["produto_id"],
                          "demo": self._um("produtos", id=l["produto_id"])["demo"],
                          "marketplace": mkt["codigo"], "dominios": mkt["dominios_afiliado"],
                          "padrao": mkt.get("padrao_afiliado"), "dominios_destino": mkt.get("dominios_destino"),
                          "parametros_afiliado": mkt.get("parametros_afiliado") or [], "id_afiliado": mkt.get("id_afiliado")})
        return saida[:limite]

    def registrar_validacao(self, link_id, resultado):
        l = self._um("links", id=link_id)
        mkt = self._mkt_do_produto(l["produto_id"])
        if resultado.valido and not _dominio_ok(l["url"], mkt["dominios_afiliado"]):
            raise RegraViolada("domínio não é de afiliado", codigo="P0011")
        if resultado.valido and mkt.get("padrao_afiliado") and not re.match(mkt["padrao_afiliado"], l["url"]):
            raise RegraViolada("link sem atribuição de afiliado", codigo="P0024")
        mudou = l["validado"] != resultado.valido
        l.update(validado=resultado.valido, status_http=resultado.status_http,
                 motivo_invalido=None if resultado.valido else resultado.motivo,
                 atribuicao_verificada_em=self.relogio() if resultado.valido and resultado.atribuicao else None)
        if mudou:
            self._invalidar_analise(l["produto_id"])

    # ---------------------------------------------------------------- publicação
    def _link_valido(self, produto_id):
        return next((l for l in self.t["links"] if l["produto_id"] == produto_id and l["validado"] and l["ativo"]), None)

    def _view(self, pr):
        prod = self._um("produtos", id=pr["produto_id"])
        mkt = self._um("marketplaces", id=prod["marketplace_id"])
        link = self._link_valido(pr["produto_id"])
        return {"id": pr["id"], "status": pr["status"], "titulo": prod["titulo"], "imagem_url": prod["imagem_url"],
                "preco_promocional": pr["preco_promocional"], "preco_referencia": pr["preco_referencia"],
                "desconto_pct": pr["desconto_pct"], "marketplace": mkt["codigo"], "marketplace_nome": mkt["nome"],
                "link_id": link["id"] if link else None, "link_validado": bool(link), "demo": pr["demo"],
                "origem": pr["origem"], "publicada_em": pr["publicada_em"], "produto_id": prod["id"],
                "pontuacao": pr.get("pontuacao"), "classificacao": pr.get("classificacao"), "analise": pr.get("analise"),
                "preco_no_pix": bool(pr.get("preco_no_pix")),
                "categoria_id": prod.get("categoria_id"),
                "categoria": (self._um("categorias", id=prod.get("categoria_id")) or {}).get("nome")}

    def promocoes_para_enfileirar(self, limite):
        na_fila = {f["promocao_id"] for f in self.t["fila"] if f["status"] in ("pendente", "processando")}
        lista = [self._view(pr) for pr in self.t["promocoes"] if pr["status"] == "aprovada" and pr["id"] not in na_fila]
        lista = [v for v in lista if v["link_validado"] and v["classificacao"] in ("excelente", "boa")]
        return sorted(lista, key=lambda v: (v["pontuacao"] or 0, v["desconto_pct"]), reverse=True)[:limite]

    def enfileirar(self, promocao_id, canal_id, link_id, prioridade=5, agendado_para=None):
        pr = self._um("promocoes", id=promocao_id)
        canal = self._um("canais", id=canal_id)
        link = self._um("links", id=link_id)
        if not (link and link["validado"] and link["ativo"] and link["produto_id"] == pr["produto_id"]):
            return False
        if pr["status"] not in ("aprovada", "publicada") or not canal["ativo"] or (pr["demo"] and canal["modo"] == "real"):
            return False
        if pr.get("classificacao") not in ("excelente", "boa"):
            return False  # banco: P0019
        horas = float(self.config.get("inteligencia.janela_repeticao_horas", 24))
        if self.publicado_no_canal_recentemente(pr["produto_id"], canal_id, horas):
            return False  # banco: P0018
        if any(f["promocao_id"] == promocao_id and f["canal_id"] == canal_id and f["status"] in ("pendente", "processando")
               for f in self.t["fila"]):
            return False
        self.t["fila"].append({"id": self._id(), "promocao_id": promocao_id, "canal_id": canal_id,
                               "link_afiliado_id": link_id, "prioridade": prioridade, "status": "pendente",
                               "tentativas": 0, "max_tentativas": 3, "agendado_para": agendado_para or self.relogio()})
        return True

    def pegar_fila(self, limite):
        agora_ = self.relogio()
        for f in self.t["fila"]:
            vencida = f["status"] == "processando" and f.get("bloqueado_ate") and f["bloqueado_ate"] < agora_
            if vencida and f.get("publicando_desde"):
                f.update(status="falhou", bloqueado_ate=None,
                         ultimo_erro="Resultado incerto: o bot parou durante o envio — confira o canal antes de publicar de novo.")
            elif vencida and f["tentativas"] >= f["max_tentativas"]:
                f.update(status="falhou", bloqueado_ate=None, ultimo_erro=f.get("ultimo_erro") or "Tempo esgotado")
        livres = [f for f in self.t["fila"] if f["tentativas"] < f["max_tentativas"] and (
            (f["status"] == "pendente" and f.get("agendado_para", agora_) <= agora_)
            or (f["status"] == "processando" and f.get("bloqueado_ate") and f["bloqueado_ate"] < agora_))]
        itens = sorted(livres, key=lambda f: f["prioridade"])[:limite]
        for f in itens:
            f.update(status="processando", tentativas=f["tentativas"] + 1, publicando_desde=None,
                     bloqueado_ate=agora_ + timedelta(minutes=10))
        return [ItemFila(f["id"], f["promocao_id"], f["canal_id"], f["link_afiliado_id"], f["tentativas"]) for f in itens]

    def fila_atrasada(self, minutos):
        from datetime import timedelta
        limite = self.relogio() - timedelta(minutes=minutos)
        return sum(1 for f in self.t["fila"] if f["status"] == "pendente" and f.get("agendado_para", self.relogio()) < limite)

    def parada_emergencia(self):
        return bool(self.config.get("sistema.parada_emergencia"))

    def produto(self, produto_id):
        p = self._um("produtos", id=produto_id)
        if not p:
            return None
        mkt = self._um("marketplaces", id=p["marketplace_id"])
        return {"id": p["id"], "id_externo": p["id_externo"], "marketplace": mkt["codigo"], "preco_atual": p["preco_atual"],
                "disponivel": p["disponivel"], "verificado_em": p.get("verificado_em")}

    def marcar_publicando(self, fila_id):
        f = self._um("fila", id=fila_id)
        if f and f["status"] == "processando":
            f["publicando_desde"] = self.relogio()

    def reservar_envio(self, fila_id):
        f = self._um("fila", id=fila_id)
        if not f or f["status"] != "processando" or f.get("publicando_desde"):
            return False
        if f.get("bloqueado_ate") and f["bloqueado_ate"] <= self.relogio():
            return False
        f["publicando_desde"] = self.relogio()
        return True

    def reservar_envio_cupom(self, cupom_id, canal_id):
        c, canal = self._um("cupons", id=cupom_id), self._um("canais", id=canal_id)
        dia = self.relogio().astimezone(timezone(timedelta(hours=-3))).date().isoformat()
        meus = [p for p in self.t["cupons_pub"] if p["cupom_id"] == cupom_id and p["canal_id"] == canal_id and p["dia"] == dia]
        reservas = sum(1 for p in meus if p["resultado"] == "enviando")
        fim = sum(1 for p in meus if p["resultado"] in ("sucesso", "erro"))
        if any(p["resultado"] == "sucesso" for p in meus) or reservas > fim:
            return None
        if not self._vigente(c):
            raise RegraViolada("cupom fora da validade ou pausado", codigo="P0025")
        if c["demo"] and canal["modo"] == "real":
            raise RegraViolada("cupom demo em canal real", codigo="P0015")
        r = {"id": self._id(), "cupom_id": cupom_id, "canal_id": canal_id, "dia": dia, "resultado": "enviando",
             "id_mensagem": None, "erro": None, "demo": c["demo"] or canal["modo"] == "demo"}
        self.t["cupons_pub"].append(r)
        return r["id"]

    def esquema_publicacao(self):
        return dict(self.esquema)

    def link_atribuicao(self, link_id):
        l = self._um("links", id=link_id)
        return l.get("atribuicao_verificada_em") if l else None

    def devolver_fila(self, fila_id, minutos, motivo):
        f = self._um("fila", id=fila_id)
        if f and f["status"] == "processando":
            f.update(status="pendente", tentativas=max(f["tentativas"] - 1, 0), bloqueado_ate=None, publicando_desde=None,
                     agendado_para=self.relogio() + timedelta(minutes=max(1, min(int(minutos), 1440))), ultimo_erro=motivo)

    def marcar_incerta(self, fila_id, motivo):
        f = self._um("fila", id=fila_id)
        if f and f["status"] in ("pendente", "processando"):
            f.update(status="falhou", bloqueado_ate=None,
                     ultimo_erro=f"Resultado incerto: {motivo} — confira o canal antes de publicar de novo.")

    def dados_publicacao(self, promocao_id):
        pr = self._um("promocoes", id=promocao_id)
        return self._view(pr) if pr else None

    def registrar_publicacao(self, item, sucesso, id_mensagem, erro, extras=None):
        pr = self._um("promocoes", id=item.promocao_id)
        canal = self._um("canais", id=item.canal_id)
        link = self._um("links", id=item.link_afiliado_id)
        demo = canal["modo"] == "demo" or pr["demo"]
        if sucesso and not (link["validado"] and link["ativo"]):
            raise RegraViolada("link não validado", codigo="P0010")
        if sucesso and pr["demo"] and canal["modo"] == "real":
            raise RegraViolada("demo em canal real", codigo="P0015")
        prod = self._um("produtos", id=pr["produto_id"])
        self.t["historico"].append({"id": self._id(), "fila_id": item.id, "promocao_id": item.promocao_id,
                                    "canal_id": item.canal_id, "resultado": "sucesso" if sucesso else "erro",
                                    "id_mensagem_externa": id_mensagem, "erro": erro, "demo": demo,
                                    "publicado_em": self.relogio(), "preco_publicado": pr["preco_promocional"],
                                    "categoria_id": prod.get("categoria_id") if prod else None,
                                    "experimento_id": (extras or {}).get("experimento_id"),
                                    "variante": (extras or {}).get("variante")})
        if sucesso and (not demo or pr["demo"]) and pr["status"] != "publicada":
            pr.update(status="publicada", publicada_em=self.relogio())
        f = self._um("fila", id=item.id)
        if f:
            f["status"] = "concluida" if sucesso else ("falhou" if f["tentativas"] >= f["max_tentativas"] else "pendente")
            f.update(bloqueado_ate=None, publicando_desde=None, ultimo_erro=None if sucesso else erro)
            if not sucesso:
                f["agendado_para"] = self.relogio() + timedelta(minutes=2 ** max(f["tentativas"], 1))

    def publicacoes_desde(self, desde, tipos):
        ids = {c["id"] for c in self.t["canais"] if c["tipo"] in tipos}
        return [h for h in self.t["historico"] if h["canal_id"] in ids and h["resultado"] == "sucesso"
                and not h["demo"] and h["publicado_em"] >= desde]

    def melhor_publicada_desde(self, desde):
        lista = [self._view(pr) for pr in self.t["promocoes"]
                 if pr["status"] == "publicada" and pr["publicada_em"] and pr["publicada_em"] >= desde]
        lista = [v for v in lista if v["link_validado"] and v["classificacao"] in ("excelente", "boa")]
        return max(lista, key=lambda v: (v["pontuacao"] or 0, v["desconto_pct"])) if lista else None

    # ---------------------------------------------------------------- inteligência
    def promocoes_para_analisar(self, limite):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] not in ("encontrada", "aprovada", "publicada") or pr["analisada_em"] is not None:
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            mkt = self._um("marketplaces", id=prod["marketplace_id"])
            cat = self._um("categorias", id=prod["categoria_id"]) if prod.get("categoria_id") else None
            links = sorted([l for l in self.t["links"] if l["produto_id"] == prod["id"] and l["ativo"]],
                           key=lambda l: l["validado"], reverse=True)
            saida.append({
                "promocao_id": pr["id"], "produto_id": prod["id"], "status": pr["status"], "origem": pr["origem"],
                "demo": pr["demo"], "preco": pr["preco_promocional"], "preco_referencia": pr["preco_referencia"],
                "referencia_origem": pr.get("referencia_origem"),
                "comissao_pct": pr.get("comissao_pct"), "titulo": prod["titulo"], "preco_produto": prod["preco_atual"],
                "disponivel": prod["disponivel"], "nota": prod.get("nota"), "total_vendas": prod.get("total_vendas"),
                "imagem_url": prod.get("imagem_url"), "categoria_ativa": cat["ativa"] if cat else None,
                "preco_maximo_variacao": prod.get("preco_maximo_variacao"), "referencia_derivada": bool(prod.get("referencia_derivada")),
                "marketplace": mkt["codigo"], "marketplace_modo": mkt["modo"], "link": links[0] if links else None})
        return saida[:limite]

    def historico_precos(self, produto_ids, desde):
        saida = {}
        for h in self.t["precos"]:
            if h["produto_id"] in produto_ids and h["quando"] >= desde:
                saida.setdefault(h["produto_id"], []).append(dict(h, ancora=False))
        for pid in produto_ids:   # âncora: o último preço antes da janela (espelho de historico_precos_janela)
            antes = [h for h in self.t["precos"] if h["produto_id"] == pid and h["quando"] < desde]
            if antes:
                saida.setdefault(pid, []).insert(0, dict(max(antes, key=lambda h: h["quando"]), ancora=True))
        return saida

    def titulos_ativos(self, desde):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] in ("encontrada", "aprovada", "publicada") and pr["criado_em"] >= desde:
                prod = self._um("produtos", id=pr["produto_id"])
                saida.append({"promocao_id": pr["id"], "produto_id": prod["id"], "titulo": prod["titulo"],
                              "marketplace": self._um("marketplaces", id=prod["marketplace_id"])["nome"]})
        return saida

    def salvar_analise(self, promocao_id, pontuacao, classificacao, confianca, analise):
        self._um("promocoes", id=promocao_id).update(pontuacao=pontuacao, classificacao=classificacao,
                                                    confianca=confianca, analise=analise, analisada_em=self.relogio())

    def publicado_no_canal_recentemente(self, produto_id, canal_id, horas):
        limite = self.relogio() - timedelta(hours=horas)
        promos = {p["id"] for p in self.t["promocoes"] if p["produto_id"] == produto_id}
        return any(h["promocao_id"] in promos and h["canal_id"] == canal_id and h["resultado"] == "sucesso"
                   and not h["demo"] and h["publicado_em"] >= limite for h in self.t["historico"])

    # ---------------------------------------------------------------- monitor
    def publicadas_para_monitorar(self, dias, limite):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] != "publicada":
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            mkt = self._um("marketplaces", id=prod["marketplace_id"])
            link = self._link_valido(prod["id"])
            saida.append({"promocao_id": pr["id"], "produto_id": prod["id"], "preco_promocional": pr["preco_promocional"],
                          "id_externo": prod["id_externo"], "marketplace": mkt["codigo"],
                          "dominios": mkt["dominios_afiliado"], "dominios_destino": mkt.get("dominios_destino") or [],
                          "verificado_em": prod.get("verificado_em"), "link_id": link["id"] if link else None,
                          "link_url": link["url"] if link else None})
        return saida[:limite]

    def atualizar_produto(self, produto_id, preco, disponivel, preco_original):
        p = self._um("produtos", id=produto_id)
        antes_preco, antes_disp = p["preco_atual"], p["disponivel"]
        antes_original = p.get("preco_original")
        p.update(preco_atual=preco, disponivel=disponivel, verificado_em=self.relogio())
        if preco_original:
            p["preco_original"] = preco_original
        if preco != antes_preco or disponivel != antes_disp or p.get("preco_original") != antes_original:
            self._registrar_preco(p)
            self._invalidar_analise(produto_id)
        if not self.config.get("monitor.ativo", True):
            return
        tol = float(self.config.get("monitor.tolerancia_preco_pct", 5))
        for pr in self.t["promocoes"]:
            if pr["produto_id"] != produto_id or pr["status"] not in ("encontrada", "aprovada", "publicada"):
                continue
            if antes_disp and not disponivel:
                self._pausar(pr, "Produto indisponível")
            elif preco > antes_preco and preco > pr["preco_promocional"] * (1 + tol / 100):
                self._pausar(pr, f"Preço subiu para R$ {preco:.2f}")

    def _pausar(self, pr, motivo):
        pr.update(status="pausada", motivo_status=motivo, pausada_em=self.relogio())
        for f in self.t["fila"]:
            if f["promocao_id"] == pr["id"] and f["status"] in ("pendente", "processando"):
                f["status"] = "cancelada"

    def invalidar_link(self, link_id, motivo):
        l = self._um("links", id=link_id)
        l.update(validado=False, motivo_invalido=motivo)
        self._invalidar_analise(l["produto_id"])
        if not self._link_valido(l["produto_id"]):
            for pr in self.t["promocoes"]:
                if pr["produto_id"] == l["produto_id"] and pr["status"] in ("aprovada", "publicada"):
                    self._pausar(pr, "Sem link de afiliado validado")

    def pausadas_com_mensagens(self, desde):
        saida = []
        for pr in self.t["promocoes"]:
            if pr["status"] == "pausada" and pr["pausada_em"] and pr["pausada_em"] >= desde:
                for h in self.t["historico"]:
                    if h["promocao_id"] == pr["id"] and h["resultado"] == "sucesso" and h["id_mensagem_externa"] and not h["demo"]:
                        saida.append({"promocao_id": pr["id"], "canal_id": h["canal_id"],
                                      "id_mensagem": h["id_mensagem_externa"], "motivo": pr["motivo_status"],
                                      "titulo": self._um("produtos", id=pr["produto_id"])["titulo"]})
        return saida

    # ---------------------------------------------------------------- comissões, execução, logs
    def salvar_comissoes(self, marketplace_id, itens):
        r = {"novos": 0, "atualizados": 0, "ignorados": 0}
        for i in {i["referencia_externa"]: i for i in itens if i.get("referencia_externa")}.values():
            antes = self._um("comissoes", marketplace_id=marketplace_id, referencia_externa=i["referencia_externa"])
            if antes and antes["status"] == "paga":
                r["ignorados"] += 1
            elif antes:
                if antes["status"] != i.get("status") or antes["valor_comissao"] != i["valor_comissao"]:
                    r["atualizados"] += 1
                antes.update({k: v for k, v in i.items()})
            else:
                nova = dict(i, marketplace_id=marketplace_id, origem="api")
                self._atribuir_comissao(nova)
                self.t["comissoes"].append(nova)
                r["novos"] += 1
        return r

    def _atribuir_comissao(self, c):
        """Espelho do gatilho privado.comissao_atribuir (migration 019)."""
        from datetime import date as _date
        if not c.get("produto_id") and c.get("id_externo_item"):
            p = self._um("produtos", marketplace_id=c["marketplace_id"], id_externo=c["id_externo_item"])
            c["produto_id"] = p["id"] if p else None
        if not c.get("produto_id"):
            c["atribuicao"] = "item_desconhecido"
            return
        venda = _date.fromisoformat(c["data_venda"]) if isinstance(c["data_venda"], str) else c["data_venda"]
        promos = {pr["id"] for pr in self.t["promocoes"] if pr["produto_id"] == c["produto_id"]}
        pubs = [h for h in self.t["historico"] if h["promocao_id"] in promos and h["resultado"] == "sucesso"
                and not h["demo"] and h["publicado_em"].date() <= venda]
        c["promocao_id"] = max(pubs, key=lambda h: h["publicado_em"])["promocao_id"] if pubs else None
        c["atribuicao"] = "oferta_publicada" if c["promocao_id"] else "produto_sem_publicacao"

    def comissoes_api_periodo(self, marketplace_id, inicio, fim):
        return [c for c in self.t["comissoes"] if c["marketplace_id"] == marketplace_id and c.get("origem") == "api"
                and inicio <= c["data_venda"] <= fim]

    def registrar_importacao(self, dados):
        self.t["comissoes_importacoes"].append(dict(dados, criado_em=self.relogio()))

    def ultima_importacao(self, marketplace_id, tipo):
        datas = [i["criado_em"] for i in self.t["comissoes_importacoes"]
                 if i["marketplace_id"] == marketplace_id and i["tipo"] == tipo and i["status"] != "erro"]
        return max(datas) if datas else None

    def iniciar_execucao(self, origem, modo, versao):
        e = {"id": str(uuid.uuid4()), "origem": origem, "modo": modo, "versao": versao, "status": "rodando",
             "iniciada_em": self.relogio(), "resumo": {}}
        self.t["execucoes"].append(e)
        return e["id"]

    def finalizar_execucao(self, execucao_id, status, resumo):
        e = self._um("execucoes", id=execucao_id)
        e.update(status=status, resumo=resumo, finalizada_em=self.relogio())

    # ---- cupons (espelha a migration 016)
    def criar_cupom(self, marketplace: str, titulo: str, fim_em, **campos) -> int:
        """Como o cadastro do painel. Aplica as mesmas regras do banco."""
        mkt = self._um("marketplaces", codigo=marketplace)
        c = {"id": self._id(), "marketplace_id": mkt["id"], "tipo": "cupom", "origem": "manual", "id_externo": None,
             "titulo": titulo.strip(), "codigo": None, "desconto_tipo": None, "desconto_valor": None, "valor_minimo": None,
             "desconto_maximo": None, "link_afiliado": None, "imagem_url": None, "palavras_chave": [], "categoria_id": None,
             "regras": None, "inicio_em": self.relogio() - timedelta(minutes=1), "fim_em": fim_em, "status": "ativo",
             "demo": mkt["modo"] == "demo", "atualizado_em": self.relogio()}
        c.update(campos)
        c["codigo"] = (c["codigo"] or "").strip().upper() or None
        self._regras_cupom(c, mkt)
        self.t["cupons"].append(c)
        return c["id"]

    def _regras_cupom(self, c, mkt):
        if c["tipo"] == "cupom" and not c["codigo"]:
            raise RegraViolada("cupom precisa de código", codigo="23514")
        if c["tipo"] == "campanha" and not c["link_afiliado"]:
            raise RegraViolada("campanha precisa de link", codigo="23514")
        if c["fim_em"] <= c["inicio_em"]:
            raise RegraViolada("validade invertida", codigo="23514")
        if c["desconto_tipo"] == "percentual" and (c["desconto_valor"] or 0) > 90:
            raise RegraViolada("desconto acima de 90%", codigo="23514")
        if c["link_afiliado"]:
            if not _dominio_ok(c["link_afiliado"], mkt["dominios_afiliado"]):
                raise RegraViolada("domínio não é de afiliado", codigo="P0011")
            if mkt.get("padrao_afiliado") and not re.match(mkt["padrao_afiliado"], c["link_afiliado"]):
                raise RegraViolada("link sem atribuição de afiliado", codigo="P0024")
        if c["status"] == "ativo" and c["fim_em"] <= self.relogio():
            c["status"] = "expirado"

    def _cupom_obj(self, c) -> Cupom:
        mkt = self._um("marketplaces", id=c["marketplace_id"])
        return Cupom.de_linha({**c, "marketplace_codigo": mkt["codigo"], "marketplace_nome": mkt["nome"]})

    def _vigente(self, c) -> bool:
        return c["status"] == "ativo" and c["inicio_em"] <= self.relogio() < c["fim_em"]

    def cupons_vigentes(self):
        return [self._cupom_obj(c) for c in sorted(self.t["cupons"], key=lambda c: c["fim_em"]) if self._vigente(c)]

    def cupons_publicados_no_dia(self, dia):
        return [{k: p[k] for k in ("cupom_id", "canal_id", "resultado", "erro")} for p in self.t["cupons_pub"] if p["dia"] == dia]

    def registrar_publicacao_cupom(self, cupom_id, canal_id, sucesso, id_mensagem, erro):
        c, canal = self._um("cupons", id=cupom_id), self._um("canais", id=canal_id)
        dia = self.relogio().astimezone(timezone(timedelta(hours=-3))).date().isoformat()
        if sucesso:
            if not self._vigente(c):
                raise RegraViolada("cupom fora da validade ou pausado", codigo="P0025")
            if c["demo"] and canal["modo"] == "real":
                raise RegraViolada("cupom demo em canal real", codigo="P0015")
            if any(p["cupom_id"] == cupom_id and p["canal_id"] == canal_id and p["dia"] == dia and p["resultado"] == "sucesso"
                   for p in self.t["cupons_pub"]):
                raise RegraViolada("cupom já publicado hoje neste canal", codigo="23505")
        self.t["cupons_pub"].append({"id": self._id(), "cupom_id": cupom_id, "canal_id": canal_id, "dia": dia,
                                     "resultado": "sucesso" if sucesso else "erro", "id_mensagem": id_mensagem,
                                     "erro": None if sucesso else erro, "demo": c["demo"] or canal["modo"] == "demo"})

    def salvar_campanha(self, cp):
        mkt = self._um("marketplaces", codigo=cp.marketplace)
        existente = self._um("cupons", marketplace_id=mkt["id"], id_externo=cp.id_externo)
        dados = {"tipo": "campanha", "origem": "automatica", "id_externo": cp.id_externo, "titulo": cp.titulo,
                 "link_afiliado": cp.link_afiliado, "fim_em": cp.fim_em, "imagem_url": cp.imagem_url}
        if existente:
            novo = {**existente, **dados, "atualizado_em": self.relogio()}
            self._regras_cupom(novo, mkt)
            existente.update(novo)
        else:
            self.criar_cupom(cp.marketplace, cp.titulo, cp.fim_em, **{k: v for k, v in dados.items() if k not in ("titulo", "fim_em")},
                             **({"inicio_em": cp.inicio_em} if cp.inicio_em and cp.inicio_em < cp.fim_em else {}))

    def campanhas_atualizadas_desde(self, desde):
        return sum(1 for c in self.t["cupons"] if c["origem"] == "automatica" and c["atualizado_em"] >= desde)

    def vincular_produto_cupom(self, cupom_id, promocao_id, origem="manual"):
        """Como o painel (manual = aprovado na hora)."""
        c = self._um("cupons", id=cupom_id)
        pr = self._um("promocoes", id=promocao_id)
        if self._um("produtos", id=pr["produto_id"])["marketplace_id"] != c["marketplace_id"]:
            raise RegraViolada("cupom de outra loja", codigo="P0026")
        if not self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id):
            self.t["cupons_produtos"].append({"cupom_id": cupom_id, "promocao_id": promocao_id, "origem": origem,
                                              "aprovado": origem == "manual", "motivo": None})

    def candidatos_para_cupom(self, cupom, limite):
        ja = {l["promocao_id"] for l in self.t["cupons_produtos"] if l["cupom_id"] == cupom.id}
        saida = []
        for pr in self.t["promocoes"]:
            if pr["id"] in ja or pr["status"] not in ("encontrada", "aprovada", "publicada"):
                continue
            if pr.get("classificacao") not in ("excelente", "boa") or bool(pr["demo"]) != cupom.demo:
                continue
            v = self._view(pr)
            if v["marketplace"] != cupom.marketplace:
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            saida.append({"id": pr["id"], "titulo": prod["titulo"], "categoria_id": prod.get("categoria_id"),
                          "preco_promocional": pr["preco_promocional"], "pontuacao": pr.get("pontuacao"), "demo": pr["demo"]})
        return sorted(saida, key=lambda x: x["pontuacao"] or 0, reverse=True)[: max(limite * 20, 100)]

    def sugerir_produto_cupom(self, cupom_id, promocao_id, motivo):
        if not self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id):
            self.vincular_produto_cupom(cupom_id, promocao_id, origem="sugestao")
            self._um("cupons_produtos", cupom_id=cupom_id, promocao_id=promocao_id)["motivo"] = motivo

    def cupons_da_promocao(self, promocao_id):
        ids = {l["cupom_id"] for l in self.t["cupons_produtos"] if l["promocao_id"] == promocao_id and l["aprovado"]}
        return [self._cupom_obj(c) for c in self.t["cupons"] if c["id"] in ids and self._vigente(c)]

    def encerrar_execucoes_orfas(self, minutos=30):
        n = 0
        for e in self.t["execucoes"]:
            if e["status"] == "rodando" and e["iniciada_em"] < self.relogio() - timedelta(minutes=max(minutos, 10)):
                e.update(status="erro", finalizada_em=self.relogio(), resumo={**e["resumo"], "interrompida": True})
                n += 1
        return n

    def ultima_execucao(self):
        feitas = [e for e in self.t["execucoes"] if e["status"] != "rodando"]
        return max(feitas, key=lambda e: e["iniciada_em"]) if feitas else None

    def gravar_logs(self, execucao_id, itens):
        self.t["logs"] += [dict(i, execucao_id=execucao_id) for i in itens]

    def obter_credencial(self, integracao, nome):
        return self.cofre.get((integracao, nome))

    # ---------------------------------------------------------------- inteligência comercial (espelho da 019)
    def dados_ranking(self, promocao_ids):
        saida = {}
        for pid in promocao_ids:
            pr = self._um("promocoes", id=pid)
            if not pr:
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            mkt = self._um("marketplaces", id=prod["marketplace_id"])
            cat = self._um("categorias", id=prod.get("categoria_id")) if prod.get("categoria_id") else None
            saida[pid] = dados_de_ranking({
                "id": pid, "produto_id": prod["id"], "preco_promocional": pr["preco_promocional"],
                "comissao_pct": pr.get("comissao_pct"), "pontuacao": pr.get("pontuacao"),
                "classificacao": pr.get("classificacao"), "analise": pr.get("analise"), "origem": pr.get("origem"),
                "demo": pr.get("demo"),
                "produtos": dict(prod, marketplaces={"codigo": mkt["codigo"], "modo": mkt["modo"]},
                                 categorias={"nome": cat["nome"]} if cat else None)})
        return saida

    def desempenho_publicacoes(self, desde):
        """Espelho de vw_desempenho_publicacoes."""
        from .inteligencia.aprendizado import faixa_de_preco, periodo_do_dia
        pubs = [h for h in self.t["historico"] if h["resultado"] == "sucesso" and not h["demo"] and h["publicado_em"] >= desde]
        por_oferta: dict[int, int] = {}
        for h in self.t["historico"]:
            if h["resultado"] == "sucesso" and not h["demo"]:
                por_oferta[h["promocao_id"]] = por_oferta.get(h["promocao_id"], 0) + 1
        vendas: dict[int, list] = {}
        for c in self.t["comissoes"]:
            if c.get("status") != "cancelada" and c.get("promocao_id") and c.get("atribuicao") == "oferta_publicada":
                vendas.setdefault(c["promocao_id"], []).append(float(c["valor_comissao"]))
        saida = []
        for h in pubs:
            canal = self._um("canais", id=h["canal_id"])
            cat = self._um("categorias", id=h.get("categoria_id")) if h.get("categoria_id") else None
            local = h["publicado_em"].astimezone(timezone(timedelta(hours=-3)))
            n = por_oferta.get(h["promocao_id"], 1)
            cli = [k for k in self.t["cliques"] if k.get("publicacao_id") == h["id"]]
            v = vendas.get(h["promocao_id"], [])
            saida.append({"publicacao_id": h["id"], "promocao_id": h["promocao_id"], "publicado_em": h["publicado_em"],
                          "canal": canal["codigo"], "hora": local.hour, "periodo": periodo_do_dia(local.hour),
                          "categoria": cat["nome"] if cat else None, "preco_publicado": h.get("preco_publicado"),
                          "faixa_preco": faixa_de_preco(h.get("preco_publicado")), "experimento_id": h.get("experimento_id"),
                          "variante": h.get("variante"), "cliques": len(cli),
                          "cliques_2h": sum(1 for k in cli if k["ocorrido_em"] < h["publicado_em"] + timedelta(hours=2)),
                          "comissao_atribuida": round(sum(v) / n, 4), "conversoes_atribuidas": round(len(v) / n, 4)})
        return sorted(saida, key=lambda l: l["publicado_em"], reverse=True)

    def salvar_selecoes(self, decisoes):
        for d in decisoes:
            self.t["selecoes"].append({"id": self._id(), "promocao_id": d["promocao_id"], "decidido_em": self.relogio(),
                                       "selecionada": d["selecionada"], "posicao": d.get("posicao"),
                                       "prioridade": d.get("prioridade"), "receita_esperada": d.get("receita_esperada"),
                                       "detalhes": d})

    def salvar_equivalencias(self, achados):
        for a in achados:
            atual = self._um("equivalentes", produto_a=a["produto_a"], produto_b=a["produto_b"])
            if atual:
                atual.update(a)
            else:
                self.t["equivalentes"].append(dict(a, id=self._id(), confirmado=None))

    def experimento_ativo(self):
        return next((dict(e) for e in self.t["experimentos"] if e.get("ativo")), None)

    def salvar_resultado_experimento(self, experimento_id, resultado):
        e = self._um("experimentos", id=experimento_id)
        if e:
            e.update(resultado=resultado, resultado_em=self.relogio())

    def salvar_resumo_inteligencia(self, tipo, dados):
        self.t["resumos"] = [r for r in self.t["resumos"] if r["tipo"] != tipo]
        self.t["resumos"].append({"tipo": tipo, "dados": dados, "calculado_em": self.relogio()})

    # ajuda dos testes: clique único registrado pela função r
    def registrar_clique_teste(self, publicacao_id: int, quando=None):
        h = self._um("historico", id=publicacao_id)
        self.t["cliques"].append({"id": self._id(), "publicacao_id": publicacao_id, "promocao_id": h["promocao_id"],
                                  "ocorrido_em": quando or self.relogio()})

    # ---------------------------------------------------------------- expansão modular (espelho da 020)
    def _hoje(self):
        return self.relogio().astimezone(timezone(timedelta(hours=-3))).date()

    def salvar_configuracao(self, chave, valor):
        self.config[chave] = valor

    def canais_categorias(self):
        saida = {}
        for l in self.t["canais_categorias"]:
            saida.setdefault(l["canal_id"], []).append(l["categoria_id"])
        return saida

    def ultimo_agendado(self, canal_id):
        datas = [f["agendado_para"] for f in self.t["fila"] if f["canal_id"] == canal_id and f["status"] in ("pendente", "processando")]
        return max(datas) if datas else None

    def tarefa_enfileirar(self, tipo, payload, chave=None, agendar_para=None):
        if chave and self._um("tarefas", tipo=tipo, chave=chave):
            return None
        t = {"id": self._id(), "tipo": tipo, "payload": payload, "chave": chave, "status": "pendente", "tentativas": 0,
             "max_tentativas": 5, "agendada_para": agendar_para or self.relogio(), "bloqueada_ate": None, "worker": None,
             "ultimo_erro": None, "resultado": None}
        self.t["tarefas"].append(t)
        return t["id"]

    def tarefas_pegar(self, tipos, limite, bloqueio_min, worker):
        agora_ = self.relogio()
        for t in self.t["tarefas"]:
            if t["status"] == "processando" and t["bloqueada_ate"] < agora_ and t["tentativas"] >= t["max_tentativas"]:
                t.update(status="falhou", bloqueada_ate=None, ultimo_erro=t["ultimo_erro"] or "Tempo esgotado no worker")
        livres = [t for t in self.t["tarefas"] if t["tipo"] in tipos and t["tentativas"] < t["max_tentativas"] and (
            (t["status"] == "pendente" and t["agendada_para"] <= agora_)
            or (t["status"] == "processando" and t["bloqueada_ate"] < agora_))]
        livres = sorted(livres, key=lambda t: (t["agendada_para"], t["id"]))[:limite]
        for t in livres:
            t.update(status="processando", tentativas=t["tentativas"] + 1, worker=worker,
                     bloqueada_ate=agora_ + timedelta(minutes=max(bloqueio_min, 1)))
        return [dict(t) for t in livres]

    def tarefa_concluir(self, tarefa_id, ok, erro, resultado, worker):
        t = self._um("tarefas", id=tarefa_id)
        if not t or t["status"] != "processando" or (worker and t["worker"] != worker):
            return False
        if ok:
            t.update(status="concluida", bloqueada_ate=None, ultimo_erro=None, resultado=resultado)
        else:
            t.update(status="falhou" if t["tentativas"] >= t["max_tentativas"] else "pendente", bloqueada_ate=None,
                     ultimo_erro=(erro or "")[:2000],
                     agendada_para=self.relogio() + timedelta(seconds=min(3600, 30 * 2 ** t["tentativas"])))
        return True

    def ofertas_vitrine(self, dias=7):
        saida = []
        for pr in self.t["promocoes"]:
            prod = self._um("produtos", id=pr["produto_id"])
            if pr["status"] != "publicada" or pr["demo"] or prod.get("demo") or not prod["disponivel"]:
                continue
            if pr.get("classificacao") not in ("excelente", "boa"):
                continue
            # espelho da migration 021: vigente, preço conferido há até N horas e sem alta desde a publicação
            if pr.get("fim_em") and pr["fim_em"] <= self.relogio():
                continue
            horas = float(self.config.get("vitrine.max_horas_sem_conferir", 24))
            conferido = prod.get("verificado_em")
            if not conferido or conferido < self.relogio() - timedelta(hours=horas):
                continue
            if prod.get("preco_atual") is None or float(prod["preco_atual"]) > float(pr["preco_promocional"]) * 1.005:
                continue
            link = self._link_valido(prod["id"])
            if not link:
                continue
            comp = bool((pr.get("analise") or {}).get("desconto_comprovado"))
            cat = self._um("categorias", id=prod.get("categoria_id")) if prod.get("categoria_id") else None
            saida.append({"promocao_id": pr["id"], "titulo": prod["titulo"], "preco": pr["preco_promocional"],
                          "preco_referencia": pr["preco_referencia"] if comp else None,
                          "desconto_pct": ((pr.get("analise") or {}).get("desconto_real") or pr["desconto_pct"]) if comp else None,
                          "desconto_comprovado": comp, "imagem_url": prod.get("imagem_url"), "marca": prod.get("marca"),
                          "categoria": cat["nome"] if cat else None,
                          "marketplace_nome": self._um("marketplaces", id=prod["marketplace_id"])["nome"],
                          "marketplace_codigo": self._um("marketplaces", id=prod["marketplace_id"])["codigo"],
                          "link_id": link["id"], "publicada_em": pr.get("publicada_em"), "conferido_em": conferido})
        return saida

    def cupons_vitrine(self):
        return [{"titulo": c.titulo, "codigo": c.codigo, "condicoes": getattr(c, "regras", None),
                 "fim_em": c.fim_em.date().isoformat() if hasattr(c.fim_em, "date") else str(c.fim_em)[:10],
                 "marketplace_nome": c.marketplace} for c in self.cupons_vigentes() if not c.demo]

    def ofertas_publicadas_desde(self, desde):
        vistos, saida = set(), []
        for h in sorted(self.t["historico"], key=lambda h: h["publicado_em"], reverse=True):
            if h["resultado"] != "sucesso" or h["demo"] or h["publicado_em"] < desde or h["promocao_id"] in vistos:
                continue
            pr = self._um("promocoes", id=h["promocao_id"])
            if pr["status"] != "publicada":
                continue
            prod = self._um("produtos", id=pr["produto_id"])
            vistos.add(pr["id"])
            saida.append({"promocao_id": pr["id"], "titulo": prod["titulo"], "preco": pr["preco_promocional"],
                          "link_id": h.get("link_afiliado_id") or self._link_valido(prod["id"])["id"],
                          "categoria_id": prod.get("categoria_id")})
        return saida

    def criar_alerta(self, chat_id, termo, preco_maximo):
        self.t["alertas"].append({"id": self._id(), "telegram_chat_id": chat_id, "termo": termo, "preco_maximo": preco_maximo, "ativo": True})

    def alertas_da_pessoa(self, chat_id):
        return [dict(a) for a in self.t["alertas"] if a["telegram_chat_id"] == chat_id and a["ativo"]]

    def apagar_alerta(self, alerta_id):
        self.t["alertas"] = [a for a in self.t["alertas"] if a["id"] != alerta_id]
        self.t["alertas_avisos"] = [v for v in self.t["alertas_avisos"] if v["alerta_id"] != alerta_id]

    def alertas_apagar_tudo(self, chat_id):
        for a in [a for a in self.t["alertas"] if a["telegram_chat_id"] == chat_id]:
            self.apagar_alerta(a["id"])

    def alertas_ativos(self):
        return [dict(a) for a in self.t["alertas"] if a["ativo"]]

    def registrar_aviso(self, alerta_id, promocao_id):
        if self._um("alertas_avisos", alerta_id=alerta_id, promocao_id=promocao_id):
            return False
        self.t["alertas_avisos"].append({"alerta_id": alerta_id, "promocao_id": promocao_id, "enviado_em": self.relogio()})
        return True

    def avisos_hoje_por_pessoa(self):
        saida = {}
        for v in self.t["alertas_avisos"]:
            a = self._um("alertas", id=v["alerta_id"])
            if a and v["enviado_em"].astimezone(timezone(timedelta(hours=-3))).date() == self._hoje():
                saida[a["telegram_chat_id"]] = saida.get(a["telegram_chat_id"], 0) + 1
        return saida

    def whatsapp_inscrever(self, telefone, origem, texto, categorias=None):
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", telefone or ""):
            raise RegraViolada("telefone fora do formato internacional", codigo="23514")
        i = self._um("wa_inscritos", telefone=telefone)
        if i:
            i.update(descadastrado_em=None, consentimento_em=self.relogio(), consentimento_origem=origem, texto_consentimento=texto)
        else:
            i = {"id": self._id(), "telefone": telefone, "categorias": list(categorias or []), "consentimento_em": self.relogio(),
                 "consentimento_origem": origem, "texto_consentimento": texto, "descadastrado_em": None}
            self.t["wa_inscritos"].append(i)
        self.whatsapp_evento(i["id"], "optin", None, origem)
        return i["id"]

    def whatsapp_descadastrar(self, telefone):
        i = self._um("wa_inscritos", telefone=telefone)
        if not i or i["descadastrado_em"]:
            return False
        i["descadastrado_em"] = self.relogio()
        self.whatsapp_evento(i["id"], "optout", None, None)
        return True

    def whatsapp_destinatarios(self, categoria_id, max_dia, promocao_id):
        saida = []
        for i in self.t["wa_inscritos"]:
            if i["descadastrado_em"] or (i["categorias"] and categoria_id not in i["categorias"]):
                continue
            if self._um("wa_envios", inscrito_id=i["id"], promocao_id=promocao_id):
                continue
            hoje = sum(1 for e in self.t["wa_eventos"] if e["inscrito_id"] == i["id"] and e["tipo"] == "envio"
                       and e["ocorrido_em"].astimezone(timezone(timedelta(hours=-3))).date() == self._hoje())
            if hoje < max_dia:
                saida.append({"id": i["id"], "telefone": i["telefone"]})
        return saida

    def whatsapp_reservar(self, inscrito_id, promocao_id):
        if self._um("wa_envios", inscrito_id=inscrito_id, promocao_id=promocao_id):
            return False
        self.t["wa_envios"].append({"inscrito_id": inscrito_id, "promocao_id": promocao_id, "reservado_em": self.relogio()})
        return True

    # ------------------------------------------------ espelho da migration 021 (limites atômicos, fila)
    def _orc(self, servico):
        o = self._um("orcamento", servico=servico, dia=self._hoje())
        if not o:
            o = {"servico": servico, "dia": self._hoje(), "gasto": 0.0, "teto": 0.0, "alertado": False}
            self.t["orcamento"].append(o)
        return o

    def orcamento_reservar(self, servico, valor, teto):
        o = self._orc(servico)
        o["teto"] = float(teto)
        if round(o["gasto"] + float(valor), 6) > float(teto):
            return False
        o["gasto"] = round(o["gasto"] + float(valor), 4)
        return True

    def orcamento_estornar(self, servico, valor):
        o = self._orc(servico)
        o["gasto"] = max(0.0, round(o["gasto"] - abs(float(valor)), 4))

    def orcamento_gasto_mes_antes_de_hoje(self, servico):
        hoje = self._hoje()
        return round(sum(o["gasto"] for o in self.t["orcamento"] + self.t["orcamento_historico"]
                         if o["servico"] == servico and o["dia"] < hoje and o["dia"].year == hoje.year
                         and o["dia"].month == hoje.month), 6)

    # ---------------------------------------------------------------- motor de IA
    def ia_prompt_ativo(self, chave):
        p = next((x for x in self.t["ia_prompts"] if x["chave"] == chave and x["ativo"]), None)
        return dict(p) if p else None

    def ia_ultimas_chamadas(self, n):
        cs = [c for c in self.t["ia_chamadas"] if c["resultado"] in ("sucesso", "erro", "invalido")]
        return [dict(c) for c in sorted(cs, key=lambda c: c["id"], reverse=True)[:n]]

    def ia_promocoes_sem_conteudo(self, prompt_id, limite):
        feitas = {c["promocao_id"] for c in self.t["ia_conteudos"] if c["prompt_id"] == prompt_id}
        prs = sorted((p for p in self.t["promocoes"] if p["status"] == "aprovada" and p["id"] not in feitas),
                     key=lambda p: p["criado_em"])
        return [p["id"] for p in prs][:limite]

    def ia_conteudo_por_chave(self, chave):
        c = self._um("ia_conteudos", chave_idem=chave)
        return dict(c) if c else None

    def ia_tentativas_invalidas(self, promocao_id, prompt_id):
        return sum(1 for c in self.t["ia_chamadas"] if c["promocao_id"] == promocao_id and c["prompt_id"] == prompt_id
                   and c["resultado"] == "invalido")

    def ia_registrar_chamada(self, prompt_id, promocao_id, modelo, resultado, tokens_entrada, tokens_saida, custo, erro):
        c = {"id": self._id(), "quando": self.relogio(), "prompt_id": prompt_id, "promocao_id": promocao_id, "modelo": modelo,
             "resultado": resultado, "tokens_entrada": tokens_entrada, "tokens_saida": tokens_saida,
             "custo_usd": round(float(custo), 6), "erro": erro}
        self.t["ia_chamadas"].append(c)
        return c["id"]

    def ia_salvar_conteudo(self, chave, promocao_id, prompt_id, chamada_id, chamada, texto, status):
        if self._um("ia_conteudos", chave_idem=chave):
            return  # idempotente (no banco: unique + ignore-duplicates)
        self.t["ia_conteudos"].append({"id": self._id(), "chave_idem": chave, "promocao_id": promocao_id, "prompt_id": prompt_id,
                                       "chamada_id": chamada_id, "chamada": chamada, "texto": texto, "status": status,
                                       "criado_em": self.relogio()})

    def ia_conteudo_aprovado(self, promocao_id):
        cs = [c for c in self.t["ia_conteudos"] if c["promocao_id"] == promocao_id and c["status"] == "aprovado"]
        return dict(max(cs, key=lambda c: c["id"])) if cs else None

    def orcamento_situacao(self, servico):
        o = self._orc(servico)
        pct = round(100 * o["gasto"] / o["teto"], 1) if o["teto"] else 0.0
        limite = float(self.config.get("financeiro.alerta_pct", 80))
        alertar = not o["alertado"] and pct >= limite
        if alertar:
            o["alertado"] = True
        return {"gasto_usd": o["gasto"], "teto_usd": o["teto"], "pct": pct, "alertar": alertar}

    def whatsapp_reservar_envio(self, inscrito_id, promocao_id, custo, teto, max_dia):
        i = self._um("wa_inscritos", id=inscrito_id)
        if not i or i["descadastrado_em"]:
            return "descadastrado"
        if self._um("wa_envios", inscrito_id=inscrito_id, promocao_id=promocao_id):
            return "repetido"
        hoje = sum(1 for e in self.t["wa_envios"] if e["inscrito_id"] == inscrito_id
                   and e.get("reservado_em", self.relogio()).astimezone(timezone(timedelta(hours=-3))).date() == self._hoje())
        if hoje >= max_dia:
            return "limite_pessoa"
        if not self.orcamento_reservar("whatsapp", custo, teto):
            self.whatsapp_evento(None, "bloqueio_orcamento", None, f"teto US$ {teto}")
            return "orcamento"
        self.t["wa_envios"].append({"inscrito_id": inscrito_id, "promocao_id": promocao_id, "reservado_em": self.relogio()})
        return "ok"

    def taxas_cancelamento(self, dias=180):
        from .repositorio import _agrupar_cancelamentos
        limite = (self.relogio() - timedelta(days=dias)).date()
        pares = []
        for c in self.t["comissoes"]:
            data_venda = c.get("data_venda")
            data_venda = data_venda.date() if hasattr(data_venda, "date") else data_venda
            if c.get("origem") == "demo" or c.get("status") not in ("confirmada", "paga", "cancelada"):
                continue
            if data_venda and str(data_venda) < str(limite):
                continue
            m = self._um("marketplaces", id=c.get("marketplace_id"))
            pares.append((m["codigo"] if m else None, c["status"]))
        return _agrupar_cancelamentos(pares)

    def tarefa_renovar(self, tarefa_id, worker, bloqueio_min):
        t = self._um("tarefas", id=tarefa_id)
        if not t or t["status"] != "processando" or t["worker"] != worker or t["bloqueada_ate"] < self.relogio():
            return False
        t["bloqueada_ate"] = self.relogio() + timedelta(minutes=max(bloqueio_min, 1))
        return True

    def fila_saude(self):
        agora_ = self.relogio()
        atraso = timedelta(minutes=float(self.config.get("fila.alerta_atraso_min", 30)))
        pend = [t for t in self.t["tarefas"] if t["status"] == "pendente" and t["agendada_para"] < agora_ - atraso]
        travas = [t for t in self.t["tarefas"] if t["status"] == "processando" and t["bloqueada_ate"] < agora_]
        falhas = [t for t in self.t["tarefas"] if t["status"] == "falhou"]
        partes = []
        if falhas:
            partes.append(f"{len(falhas)} tarefa(s) com falha definitiva nas últimas 24 h")
        if pend:
            partes.append(f"{len(pend)} tarefa(s) atrasada(s)")
        if travas:
            partes.append(f"{len(travas)} tarefa(s) abandonada(s) (trava vencida)")
        return {"pendentes_atrasadas": len(pend), "travas_vencidas": len(travas), "falhas_24h": len(falhas),
                "alerta": bool(partes), "motivo": "; ".join(partes)}

    def whatsapp_gasto_hoje(self):
        return round(sum(float(e["custo_usd"] or 0) for e in self.t["wa_eventos"]
                         if e["ocorrido_em"].astimezone(timezone(timedelta(hours=-3))).date() == self._hoje()), 4)

    def whatsapp_evento(self, inscrito_id, tipo, custo, detalhe):
        self.t["wa_eventos"].append({"inscrito_id": inscrito_id, "tipo": tipo, "custo_usd": custo, "detalhe": detalhe,
                                     "ocorrido_em": self.relogio()})
