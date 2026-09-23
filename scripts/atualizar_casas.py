#!/usr/bin/env python3
"""
Vai buscar ao INE o preço mediano por m² das casas vendidas (indicador 0012234,
últimos 12 meses, por trimestre) para Portugal e 5 concelhos, e grava tudo em
dados/casas.json. Corre todos os dias no GitHub (ver .github/workflows).

Só usa a biblioteca padrão do Python: não é preciso instalar nada.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

INE_URL = os.environ.get("INE_URL", "https://www.ine.pt/ine/json_indicador/pindica.jsp")
INDICADOR = "0012234"
FICHEIRO = os.path.join(os.path.dirname(__file__), "..", "dados", "casas.json")

# nome -> códigos geográficos do INE (NUTS 2024 primeiro; NUTS 2013 por segurança)
LOCALIDADES = {
    "Portugal":   ["PT"],
    "Lisboa":     ["1A01106", "1701106"],
    "Porto":      ["11A1312"],
    "Matosinhos": ["11A1308"],
    "Maia":       ["11A1306"],
    "Valongo":    ["11A1315"],
}
TRIMESTRES = 28  # 7 anos

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


class ErroINE(Exception):
    """O INE respondeu, mas com uma mensagem de erro (por exemplo, trimestre ainda não publicado)."""


def pedir(params: dict, tentativas: int = 3) -> list:
    url = INE_URL + "?" + urllib.parse.urlencode({"op": "2", "lang": "PT", **params})
    ultimo = None
    for n in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=CABECALHOS)
            with urllib.request.urlopen(req, timeout=180) as r:
                dados = json.loads(r.read().decode("utf-8"))
            raiz = dados[0] if isinstance(dados, list) else dados
            if isinstance(raiz, dict) and "Sucesso" in raiz and "Falso" in raiz["Sucesso"]:
                raise ErroINE(raiz["Sucesso"]["Falso"][0].get("Msg", "erro desconhecido"))
            return dados
        except ErroINE:
            raise
        except Exception as e:  # rede lenta, erro temporário…
            ultimo = e
            print(f"  tentativa {n + 1} falhou: {e}", file=sys.stderr)
            time.sleep(10 * (n + 1))
    raise RuntimeError(f"o INE não respondeu: {ultimo}")


def trimestre(rotulo: str):
    m = re.search(r"(\d)\s*\.?\s*[ºo°]?\s*trimestre\s*(?:de\s*)?(\d{4})", rotulo, re.I)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.search(r"(\d{4})\s*[-\s]?T\s*(\d)", rotulo, re.I)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    return None


def registos(dados, codigo: str) -> dict:
    raiz = dados[0] if isinstance(dados, list) else dados
    out = {}
    for rotulo, linhas in (raiz.get("Dados") or {}).items():
        q = trimestre(rotulo)
        if not q:
            continue
        for l in linhas:
            if l.get("geocod") not in (codigo, None):
                continue
            cat = l.get("dim_3_t") or l.get("dim_3") or "Total"
            if not (str(cat).lower().startswith("total") or l.get("dim_3") == "T"):
                continue
            try:
                out[q] = float(str(l.get("valor", "")).replace(" ", "").replace(",", "."))
            except ValueError:
                pass
    return out


def quarter_de_codigo(codigo: str):
    """'S5A20261' -> '2026-Q1' (aceita também '...202601')."""
    m = re.match(r"^S\dA(\d{4})0?([1-4])$", codigo)
    return f"{m.group(1)}-Q{m.group(2)}" if m else None


def codigos_da_ficha() -> list:
    """Tenta ler da ficha (metainformação) do indicador os códigos de período que existem."""
    url = INE_URL.replace("pindica.jsp", "pindicaMeta.jsp") + "?" + urllib.parse.urlencode({"varcd": INDICADOR, "lang": "PT"})
    try:
        req = urllib.request.Request(url, headers=CABECALHOS)
        with urllib.request.urlopen(req, timeout=120) as r:
            texto = r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"Ficha do indicador indisponível: {e}", file=sys.stderr)
        return []
    codigos = sorted(set(re.findall(r"S\dA\d{4}0?[1-4](?!\d)", texto)))
    codigos = [c for c in codigos if quarter_de_codigo(c)]
    print(f"Ficha do indicador: {len(codigos)} códigos de trimestre encontrados" + (f" ({codigos[0]} a {codigos[-1]})" if codigos else ""))
    return codigos


def tem_dados(dados) -> bool:
    raiz = dados[0] if isinstance(dados, list) else dados
    return bool(raiz.get("Dados"))


def descobrir_formato(hoje: datetime):
    """Experimenta formatos de código num trimestre já publicado até o INE aceitar um."""
    y, t = hoje.year, (hoje.month - 1) // 3 + 1
    candidatos_q = []
    for _ in range(6):  # recua até 6 trimestres (o mais recente pode ainda não estar publicado)
        t -= 1
        if t < 1:
            t, y = 4, y - 1
        candidatos_q.append((y, t))
    formatos = [lambda p, y, t: f"{p}{y}{t}", lambda p, y, t: f"{p}{y}0{t}"]
    prefixos = ["S5A", "S3A", "S4A", "S6A", "S2A", "S1A", "S7A", "S8A", "S9A"]
    for (yy, tt) in candidatos_q[1:3]:  # trimestres quase certamente já publicados
        for f in formatos:
            for p in prefixos:
                codigo = f(p, yy, tt)
                try:
                    dados = pedir({"varcd": INDICADOR, "Dim1": codigo, "Dim2": "PT"}, tentativas=1)
                    if tem_dados(dados):
                        print(f"Formato aceite pelo INE: {codigo}")
                        return lambda y, t, f=f, p=p: f(p, y, t)
                except ErroINE:
                    pass
                except Exception as e:
                    print(f"  {codigo}: {e}", file=sys.stderr)
    return None


def lista_trimestres(hoje: datetime, formato) -> list:
    y, t = hoje.year, (hoje.month - 1) // 3 + 1
    out = []
    for _ in range(TRIMESTRES + 2):
        out.append(formato(y, t))
        t -= 1
        if t < 1:
            t, y = 4, y - 1
    return out  # do mais recente para o mais antigo


def recolher(hoje: datetime) -> dict:
    """Pede um trimestre de cada vez (todas as localidades de uma vez) e junta os que interessam."""
    codigos = codigos_da_ficha()
    if codigos:
        codigos = sorted(codigos, reverse=True)[:TRIMESTRES]
    else:
        formato = descobrir_formato(hoje)
        if not formato:
            raise RuntimeError("não foi possível descobrir o formato dos trimestres aceite pelo INE")
        codigos = lista_trimestres(hoje, formato)

    alvo = {c: nome for nome, cs in LOCALIDADES.items() for c in cs}
    por_local = {n: {} for n in LOCALIDADES}
    obtidos, falhas_seguidas = 0, 0
    for codigo in codigos:
        try:
            dados = pedir({"varcd": INDICADOR, "Dim1": codigo})
        except ErroINE as e:
            falhas_seguidas += 1
            print(f"{codigo}: INE diz '{e}'", file=sys.stderr)
            if obtidos and falhas_seguidas >= 3:
                break  # chegámos ao início da série
            continue
        falhas_seguidas = 0
        raiz = dados[0] if isinstance(dados, list) else dados
        n_antes = sum(len(v) for v in por_local.values())
        for rotulo, linhas in (raiz.get("Dados") or {}).items():
            q = trimestre(rotulo) or quarter_de_codigo(codigo)
            for l in linhas:
                nome = alvo.get(l.get("geocod"))
                if not nome:
                    continue
                cat = l.get("dim_3_t") or l.get("dim_3") or "Total"
                if not (str(cat).lower().startswith("total") or l.get("dim_3") == "T"):
                    continue
                try:
                    por_local[nome][q] = float(str(l.get("valor", "")).replace(" ", "").replace(",", "."))
                except ValueError:
                    pass
        novos = sum(len(v) for v in por_local.values()) - n_antes
        if novos:
            obtidos += 1
        print(f"{codigo}: {novos} valores")
        time.sleep(0.5)
    return por_local


def main() -> int:
    hoje = datetime.now(timezone.utc)
    antigo = {}
    if os.path.exists(FICHEIRO):
        with open(FICHEIRO, encoding="utf-8") as f:
            antigo = json.load(f)

    try:
        por_local = recolher(hoje)
    except Exception as e:
        print(f"Falhou: {e}", file=sys.stderr)
        por_local = {}
    for nome, serie in por_local.items():
        print(f"{nome}: {len(serie)} trimestres")

    if not any(por_local.values()):
        print("Nenhum dado obtido; o ficheiro fica como estava.", file=sys.stderr)
        return 1

    # juntar com o que já havia (para não perder uma localidade se o INE falhar só nela)
    antigos = {}
    for nome, serie in (antigo.get("valores") or {}).items():
        antigos[nome] = dict(zip(antigo.get("periodos", []), serie))
    for nome in LOCALIDADES:
        if not por_local.get(nome) and antigos.get(nome):
            por_local[nome] = {k: v for k, v in antigos[nome].items() if v is not None}

    periodos = sorted({q for s in por_local.values() for q in s})
    novo = {
        "fonte": "INE, indicador 0012234 (valor mediano das vendas por m², últimos 12 meses)",
        "periodos": periodos,
        "valores": {n: [por_local.get(n, {}).get(q) for q in periodos] for n in LOCALIDADES},
    }
    if {k: antigo.get(k) for k in ("periodos", "valores")} == {k: novo[k] for k in ("periodos", "valores")}:
        print("Sem alterações.")
        return 0
    novo["atualizado"] = hoje.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(FICHEIRO), exist_ok=True)
    with open(FICHEIRO, "w", encoding="utf-8") as f:
        json.dump(novo, f, ensure_ascii=False, indent=1)
    print(f"Gravado: {len(periodos)} trimestres, último {periodos[-1]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
