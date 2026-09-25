# Boletim Económico

Painel pessoal com os principais indicadores da economia e da habitação em Portugal. Atualiza-se sozinho a partir de fontes oficiais: BCE, INE, Banco de Portugal e Eurostat.

**Abrir o painel:** https://pedrocerqueira222.github.io/painel-economico/

Cada gráfico abre com um clique no título e mostra um comentário automático, que é escrito a partir dos dados mais recentes. Exemplos: *"O dinheiro está a ficar mais caro"*, *"O poder de compra está a aumentar"*, *"A procura de casas está muito forte"*.

---

## O que mostra

### Económico

| Gráfico | Pergunta a que responde | Dados |
|---|---|---|
| Inflação vs salários | O poder de compra está a aumentar ou a diminuir? | Inflação mensal (IHPC) e remuneração por trabalhador, trimestral |
| BCE vs Euribor | O dinheiro está a ficar mais caro ou mais barato? | Taxa de depósito do BCE, Euribor a 3, 6 e 12 meses |
| PIB e desemprego | Como estão a economia e o mercado de trabalho? | Crescimento real do PIB, taxa de desemprego mensal |
| Consumo e investimento | O que está a sustentar a economia? | Crescimento real do consumo das famílias e do investimento |
| Contas com o exterior | Portugal está a ganhar ou a perder dinheiro face ao exterior? | Saldo de bens e serviços e balança corrente, em % do PIB |

### Habitação

| Gráfico | Pergunta a que responde | Dados |
|---|---|---|
| Preço das casas | Quanto custa comprar? | Preço mediano €/m² das casas vendidas: Portugal, Lisboa, Porto, Matosinhos, Maia, Valongo |
| Preços das casas vs salários | As casas estão a ficar mais caras face aos rendimentos? | Índice de preços da habitação e índice de salários, ambos a começar em 100 |
| Rendas | Quanto custa arrendar e como está a evoluir? | Renda mediana €/m² dos novos contratos, nas mesmas 6 zonas |
| Prestação do crédito à habitação | Quanto custa financiar uma casa? | Prestação de 200 000 € e 100 000 € a 30 anos, com Euribor + spread (escolhe-se no gráfico) |

### Oferta e Procura

| Gráfico | Pergunta a que responde | Dados |
|---|---|---|
| Construção | Estamos a construir casas suficientes? | Fogos licenciados e fogos concluídos em construções novas, comparados com o aumento de população vindo da migração |
| Procura | Quão forte está a procura? | Número de casas vendidas e crédito à habitação novo concedido pelos bancos |
| Pessoas que chegam a Portugal | Quantas pessoas entram e saem todos os anos? | Imigrantes, emigrantes e saldo migratório |

---

## Como funciona

```
┌──────────────────────┐        sempre que se abre a página
│   BCE (Data Portal)  │ ─────────────────────────────────────┐
└──────────────────────┘                                      ▼
                                                     ┌─────────────────┐
┌──────────────────────┐    de hora a hora, até      │                 │
│                      │    conseguir (1× por dia)   │                 │
│ INE  ·  Eurostat     │ ──► tarefa do GitHub ──►    │   index.html    │
└──────────────────────┘     (dados/*.json)  ─────►  │  (GitHub Pages) │
                                                     └─────────────────┘
```

- **Dados do BCE** (Euribor, taxas do BCE, inflação, PIB, desemprego, salários, consumo, investimento, comércio externo, balança corrente, índice de preços da habitação, crédito): a página vai buscá-los diretamente ao BCE sempre que é aberta.
- **Dados do INE e do Eurostat** (preços e rendas por concelho, construção, vendas, migração): o INE não deixa uma página no browser ler os dados diretamente, e é lento a responder. Por isso, uma tarefa automática do GitHub tenta ir buscá-los de hora a hora, até o INE responder. Quando consegue, não volta a pedir dados ao INE nas 20 horas seguintes, para não o sobrecarregar. Os dados ficam guardados na pasta `dados/`. A página lê esses ficheiros, que carregam logo.

Tudo é público e gratuito. Não é preciso nenhuma chave, conta paga ou servidor.

---

## Estrutura do repositório

```
├── index.html                          # o painel (uma única página, sem dependências a instalar)
├── scripts/
│   └── atualizar_casas.py              # vai buscar os dados ao INE e ao Eurostat
├── .github/workflows/
│   └── atualizar-casas.yml             # tarefa automática diária
└── dados/                              # criado e atualizado pela tarefa (não editar à mão)
    ├── casas.json                      # preço mediano €/m² por concelho
    ├── rendas.json                     # renda mediana €/m² por concelho
    ├── construcao.json                 # fogos licenciados e concluídos
    ├── transacoes.json                 # casas vendidas
    ├── migracao.json                   # imigrantes e emigrantes
    ├── estado.json                     # quando foi a última ida ao INE com sucesso
    └── verificado.txt                  # registo mensal (mantém a tarefa ativa)
```

---

## Fontes

| Fonte | Séries |
|---|---|
| [BCE Data Portal](https://data.ecb.europa.eu) | Euribor e taxas oficiais (FM), inflação (HICP), contas nacionais (MNA), desemprego (LFSI), preços da habitação (RESR), crédito novo (MIR), balança de pagamentos (BP6) |
| [INE](https://www.ine.pt) | Preço mediano das casas vendidas por concelho (0012234), rendas de novos contratos, fogos licenciados e concluídos (0012778), vendas de alojamentos |
| [Eurostat](https://ec.europa.eu/eurostat) | Imigração (migr_imm1ctz) e emigração (migr_emi1ctz) |

Cada gráfico tem por baixo uma nota com a definição exata dos dados e a sua origem.

**Quando saem dados novos:** a Euribor e a inflação são mensais. O PIB, os salários, o consumo, o investimento, a balança corrente e os preços das casas são trimestrais, e saem 2 a 3 meses depois do fim do trimestre. As rendas por concelho são trimestrais. A migração é anual e sai com cerca de 15 meses de atraso.

---

## Manutenção

**No dia a dia não é preciso fazer nada.** É só abrir o endereço do painel.

**Forçar uma atualização dos dados do INE e do Eurostat:**
**Actions** → *Atualizar preço das casas* → **Run workflow**. As corridas à mão vão sempre ao INE, mesmo que já tenha sido contactado nesse dia.

**Alterar a página:**
substituir o `index.html` na raiz do repositório (**Add file → Upload files**). Os dados não se perdem. Depois de 1 a 2 minutos, recarregar o painel com **Ctrl + F5**.

**Alterar o programa ou a tarefa:**
- `atualizar_casas.py` fica **dentro da pasta `scripts/`**;
- `atualizar-casas.yml` fica **dentro de `.github/workflows/`**.

Entrar na pasta certa **antes** de carregar em *Add file → Upload files*. Evitar editar o `.yml` diretamente no GitHub, porque um espaço a mais ou a menos no início de uma linha estraga o ficheiro.

---

## Problemas comuns

| O que aparece | O que quer dizer | O que fazer |
|---|---|---|
| Aviso a vermelho por baixo de um gráfico | Uma fonte não respondeu. O gráfico mostra os últimos dados guardados | Normalmente resolve-se sozinho. Carregar em *Atualizar* no gráfico |
| "Os dados ainda não existem no GitHub" | A tarefa ainda não conseguiu ir buscar esses dados ao INE | Correr a tarefa à mão (ver acima) ou esperar pelo dia seguinte |
| Na tarefa: "O INE não está acessível a partir do GitHub" | O INE não respondeu nessa hora | Nada. Os dados ficam como estavam e a tarefa tenta de novo na hora seguinte |
| Na tarefa: "Nada a fazer agora" | O INE já foi contactado com sucesso nas últimas 20 horas | Nada: é o funcionamento normal |
| O painel dá erro 404 | O GitHub Pages está desligado (acontece, por exemplo, depois de pôr o repositório privado) | **Settings → Pages** → *Deploy from a branch* → **main** / **(root)** → **Save** |
| A página mostra a versão antiga | O browser guardou a versão anterior | **Ctrl + F5** |

**Nota:** o repositório tem de ser **público** para o painel funcionar no plano gratuito do GitHub. Não contém dados pessoais, apenas dados públicos e o código da página.

---

## Limitações

- Os comentários automáticos seguem regras simples sobre os números (por exemplo, "mais caro" quando a Euribor a 12 meses sobe 0,10 pontos em três meses). Não são previsões nem aconselhamento financeiro.
- O "índice de salários" é a remuneração média por trabalhador das contas nacionais: inclui as contribuições do empregador e não é exatamente o salário líquido.
- A prestação do crédito é uma estimativa. Não inclui seguros, comissões nem imposto do selo.
- A comparação entre construção e população usa uma média de 2,5 pessoas por casa.
