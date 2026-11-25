"""Acesso seguro a URLs externas (links de afiliado e imagens).

Toda URL que o bot abre passa por aqui:
  * só https, sem usuário/senha na URL, só a porta padrão (443);
  * o nome do servidor precisa resolver SOMENTE para endereços públicos da internet
    (bloqueia 127.0.0.1, 10.x, 192.168.x, 169.254.169.254, 100.64.x, ::1, fd00:: etc.: proteção SSRF);
  * redirecionamentos são seguidos um a um (nunca automaticamente), com limite, e cada salto
    passa pelas mesmas regras;
  * downloads de imagem têm limite de bytes, de pixels, de formato e de tempo.

Risco residual: entre a checagem de DNS e a conexão, um DNS malicioso poderia trocar o endereço
(DNS rebinding). Como o bot só acessa domínios de lojas cadastrados, esse risco é baixo.
"""
from __future__ import annotations

import io
import ipaddress
import socket
import time
from typing import Callable
from urllib.parse import urljoin, urlparse

from PIL import Image

from .cliente_http import ClienteHTTP
from .erros import ErroIntegracao

Resolver = Callable[[str], list[str]]
MAX_SALTOS = 5
TIPOS_IMAGEM = {"image/jpeg": "JPEG", "image/jpg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


class UrlRecusada(Exception):
    """A URL não pode ser acessada por regra de segurança (o motivo vai na mensagem)."""


class ImagemRecusada(Exception):
    """A imagem não pode ser usada (tamanho, formato, tempo ou endereço)."""


def resolver_padrao(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


def ip_publico(endereco: str) -> bool:
    try:
        ip = ipaddress.ip_address(endereco.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def checar_url(url: str, resolver: Resolver | None = None) -> str:
    """Valida a URL antes de qualquer conexão. Devolve o host. Lança UrlRecusada."""
    try:
        p = urlparse(url)
        porta = p.port
    except ValueError:
        raise UrlRecusada("URL malformada") from None
    if p.scheme != "https":
        raise UrlRecusada(f"só links https são aceitos (recebido: {p.scheme or 'sem protocolo'})")
    if p.username or p.password or "@" in (p.netloc or ""):
        raise UrlRecusada("URL com usuário/senha embutidos")
    if porta not in (None, 443):
        raise UrlRecusada(f"porta {porta} não permitida")
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise UrlRecusada("URL sem servidor")
    enderecos = (resolver or resolver_padrao)(host)
    if not enderecos:
        raise UrlRecusada(f"não consegui resolver {host}")
    privados = [e for e in enderecos if not ip_publico(e)]
    if privados:
        raise UrlRecusada(f"{host} aponta para endereço privado/interno ({privados[0]}): bloqueado")
    return host


def host_permitido(host: str, dominios: list[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return bool(host) and any(host == d.lower() or host.endswith("." + d.lower()) for d in dominios or [])


def seguir(http: ClienteHTTP, url: str, *, resolver: Resolver | None = None, max_saltos: int = MAX_SALTOS,
           hosts_permitidos: list[str] | None = None):
    """GET seguindo redirecionamentos um a um. Devolve (resposta_final, cadeia_de_urls).
    Com `hosts_permitidos`, nenhum salto sai desses domínios (nem chega a ser acessado)."""
    cadeia = [url]
    atual = url
    for _ in range(max_saltos + 1):
        h = checar_url(atual, resolver)
        if hosts_permitidos is not None and not host_permitido(h, hosts_permitidos):
            raise UrlRecusada(f"o link redireciona para {h}, que não é um site da loja")
        resp = http.get(atual, allow_redirects=False, stream=True, ok_status=tuple(range(300, 600)))
        if not (100 <= resp.status_code <= 599):
            _fechar(resp)
            raise UrlRecusada(f"resposta HTTP inválida ({resp.status_code})")
        if 300 <= resp.status_code < 400 and resp.status_code != 304:
            destino = (resp.headers or {}).get("Location") or (resp.headers or {}).get("location")
            _fechar(resp)
            if not destino:
                raise UrlRecusada(f"redirecionamento HTTP {resp.status_code} sem destino")
            atual = urljoin(atual, destino.strip())
            cadeia.append(atual)
            continue
        return resp, cadeia
    raise UrlRecusada(f"redirecionamentos demais (mais de {max_saltos})")


def baixar_imagem(http: ClienteHTTP, url: str, *, resolver: Resolver | None = None, max_bytes: int = 5 * 1024 * 1024,
                  max_pixels: int = 40_000_000, max_lado: int = 8000, prazo_s: float = 15.0,
                  cronometro: Callable[[], float] = time.monotonic) -> bytes:
    """Baixa uma imagem com limites. Lança ImagemRecusada com o motivo."""
    inicio = cronometro()
    try:
        resp, _ = seguir(http, url, resolver=resolver, max_saltos=3)
    except UrlRecusada as e:
        raise ImagemRecusada(str(e)) from None
    except ErroIntegracao as e:
        raise ImagemRecusada(f"não abriu: {e}") from None
    try:
        if resp.status_code != 200:
            raise ImagemRecusada(f"HTTP {resp.status_code}")
        tipo = ((resp.headers or {}).get("Content-Type") or (resp.headers or {}).get("content-type") or "").split(";")[0].strip().lower()
        if tipo not in TIPOS_IMAGEM:
            raise ImagemRecusada(f"formato não aceito ({tipo or 'sem tipo'})")
        declarado = (resp.headers or {}).get("Content-Length") or (resp.headers or {}).get("content-length")
        if declarado and str(declarado).isdigit() and int(declarado) > max_bytes:
            raise ImagemRecusada(f"imagem grande demais ({int(declarado) // 1024} KB)")
        partes, total = [], 0
        for bloco in resp.iter_content(65536):
            if cronometro() - inicio > prazo_s:
                raise ImagemRecusada(f"tempo de download esgotado ({prazo_s:.0f} s)")
            total += len(bloco)
            if total > max_bytes:
                raise ImagemRecusada(f"imagem grande demais (mais de {max_bytes // 1024} KB)")
            partes.append(bloco)
    finally:
        _fechar(resp)
    dados = b"".join(partes)
    try:
        with Image.open(io.BytesIO(dados)) as img:  # Pillow já recusa imagens com pixels absurdos (bomba)
            largura, altura = img.size
            if largura * altura > max_pixels or max(largura, altura) > max_lado:
                raise ImagemRecusada(f"imagem com pixels demais ({largura}x{altura})")
            if img.format not in set(TIPOS_IMAGEM.values()):
                raise ImagemRecusada(f"formato real não aceito ({img.format})")
            img.verify()
    except ImagemRecusada:
        raise
    except Image.DecompressionBombError:
        raise ImagemRecusada("imagem com pixels demais (possível bomba de descompressão)") from None
    except Exception as e:  # arquivo corrompido ou não é imagem
        raise ImagemRecusada(f"arquivo de imagem inválido ({type(e).__name__})") from None
    if cronometro() - inicio > prazo_s:
        raise ImagemRecusada(f"tempo de processamento esgotado ({prazo_s:.0f} s)")
    return dados


def _fechar(resp) -> None:
    try:
        resp.close()
    except Exception:
        pass
