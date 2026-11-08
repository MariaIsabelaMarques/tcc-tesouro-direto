
# Tesouro Direto como política pública: código da análise

Código em Python utilizado no Trabalho de Conclusão de Curso "Tesouro Direto como política pública: desenho do programa, democratização do acesso e trade-offs de custo/risco", de Maria Isabela Marques Pereira (Administração Pública, FGV EAESP, 2026), sob orientação da Profa. Rogiene Batista dos Santos.

O script trata os microdados de investidores do Tesouro Direto e calcula os indicadores das três dimensões de democratização do acesso analisadas no trabalho: amplitude, distribuição e profundidade. Os resultados correspondem às seções 4.2 a 4.4 do TCC.

## Conteúdo do repositório

- `tcc_td.py`: script da análise.
- `dados/balanco_serie.csv`: investidores cadastrados e ativos em dezembro de cada ano, extraídos do Anexo 3.1 do Balanço do Tesouro Direto de dezembro de 2025.
- `dados/balanco_perfil.csv`: participação de mulheres e de cada região na base de cadastrados, em dezembro de cada ano a partir de 2009, extraída do mesmo anexo.

## Dados de entrada não incluídos

Os dois arquivos abaixo não estão no repositório por causa do tamanho ou por serem de terceiros. Baixe-os e salve-os na pasta `dados/` com os nomes indicados.

- `investidorestesourodireto.gz`: Tesouro Transparente, conjunto "Investidores do Tesouro Direto": https://www.tesourotransparente.gov.br/ckan/dataset/investidores-do-tesouro-direto. A análise usou a base na posição de 31/03/2026, obtida em 11/10/2026. Como a base é atualizada mensalmente e cada versão substitui a anterior, uma versão mais recente produzirá números diferentes.
- `projecoes_2024_tab1_idade_simples.xlsx`: IBGE, Projeções da População, revisão 2024, tabela "População por sexo e idade simples": https://www.ibge.gov.br/estatisticas/sociais/populacao/9109-projecao-da-opulacao

Fonte dos arquivos de balanço: Secretaria do Tesouro Nacional, Balanço do Tesouro Direto: https://www.tesourotransparente.gov.br/publicacoes/balanco-do-tesouro-direto-btd

Depois de baixar os dois arquivos de dados, a pasta do projeto deve ficar assim (os itens marcados com * não estão no repositório):

## Estrutura de pastas

```
tcc_td.py
dados/
    investidorestesourodireto.gz             *
    projecoes_2024_tab1_idade_simples.xlsx   *
    balanco_serie.csv
    balanco_perfil.csv
```

## Como executar

Testado com Python 3.13.14,  DuckDB 1.5.6 e pandas 3.0.6. Instale as bibliotecas:

```
pip install duckdb pandas pyarrow matplotlib openpyxl
```

O script roda em duas etapas, nesta ordem:

```
python tcc_td.py parte1
python tcc_td.py parte2
```

- **Parte 1** (alguns minutos): lê a base completa de investidores, padroniza os campos, aplica os filtros, consolida os registros por investidor e confronta os resultados com os balanços do Tesouro Direto. Grava uma base de trabalho local (`td.duckdb`, na pasta `tcc_td_local` do usuário) e o log `resultados/parte1_log.txt`.
- **Parte 2** (segundos): usa a base gerada na parte 1 para calcular os indicadores de amplitude, distribuição e profundidade e gerar as tabelas (`resultados/*.csv`), os gráficos (`graficos/*.png`) e o resumo dos indicadores (`resultados/resumo.txt`).

## Observações

- O recorte é de 2002 a 2025: os indicadores consideram apenas investidores que aderiram até 31/12/2025.
- A situação da conta e a informação de operação nos últimos 12 meses refletem a posição da base (31/03/2026), e não o fim de cada ano.
- O código foi escrito com auxílio de ferramenta de inteligência artificial generativa (Claude, da Anthropic), a partir das definições metodológicas do trabalho. 

## Como citar

PEREIRA, Maria Isabela Marques. tcc-tesouro-direto: código em Python para tratamento dos microdados de investidores do Tesouro Direto e cálculo dos indicadores de democratização do acesso. [S. l.]: GitHub, 2026. Disponível em: https://github.com/[seu-usuario]/tcc-tesouro-direto. Acesso em: [dia] [mês]. 2026.
