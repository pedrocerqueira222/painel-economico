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


def codigos_trimestres(recuar: int, hoje: datetime) -> list:
    y, t = hoje.year, (hoje.month - 1) // 3 + 1 - recuar
    while t < 1:
        t += 4
        y -= 1
    out = []
    for _ in range(TRIMESTRES):
        out.insert(0, f"S5A{y}{t}")
        t -= 1
        if t < 1:
            t, y = 4, y - 1
    return out


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


def buscar(nome: str, recuar: int, hoje: datetime) -> dict:
    ultimo = None
    for codigo in LOCALIDADES[nome]:
        try:
            dados = pedir({"varcd": INDICADOR, "Dim1": ",".join(codigos_trimestres(recuar, hoje)), "Dim2": codigo})
            valores = registos(dados, codigo)
            if valores:
                return valores
        except ErroINE:
            raise
        except Exception as e:
            ultimo = e
    if ultimo:
        raise ultimo
    return {}


def main() -> int:
    hoje = datetime.now(timezone.utc)
    antigo = {}
    if os.path.exists(FICHEIRO):
        with open(FICHEIRO, encoding="utf-8") as f:
            antigo = json.load(f)

    por_local = {}
    for nome in LOCALIDADES:
        for recuar in (1, 2, 3):  # se o trimestre mais recente ainda não existir, recua
            try:
                por_local[nome] = buscar(nome, recuar, hoje)
                print(f"{nome}: {len(por_local[nome])} trimestres")
                break
            except ErroINE as e:
                print(f"{nome}: INE diz '{e}', a recuar um trimestre", file=sys.stderr)
            except Exception as e:
                print(f"{nome}: falhou ({e})", file=sys.stderr)
                break

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
