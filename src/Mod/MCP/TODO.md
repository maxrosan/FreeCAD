# TODO - módulo MCP

## Projeto elétrico com dados (não só desenho)

Hoje `insert_electrical_symbol` e `draw_conduit` (`freecad_mcp/handlers/electrical.py`) só desenham.
Cada símbolo vira um `App::DocumentObjectGroup` "Simbolo" com linhas e textos Draft soltos; circuito,
potência e letra de comando existem apenas como texto ("-2-", "160VA", "a"). O eletroduto é uma linha
que não sabe de onde sai nem aonde chega, e os condutores são riscos com o circuito e a bitola em texto.
Quadro de cargas e diagrama unifilar são desenhados à parte, sem ligação com a planta.

Consequência: não dá para consultar "quais pontos são do circuito 3" ou "qual a carga da fase A", gerar
quadro de cargas ou levantamento de material, nem perceber quando planta e quadro divergem. Qualquer
mudança é apagar, redesenhar e corrigir as tabelas à mão (aconteceu na revisão da Casa 5, em 2026-11-04).

O que fazer:

- [ ] **Ponto elétrico como objeto paramétrico** (`App::FeaturePython`): tipo (símbolo NBR 5444),
      posição, rotação, circuito, potência (VA/W), altura de montagem, letra de comando, ambiente.
      O desenho é gerado a partir dessas propriedades e refeito quando elas mudam.
- [ ] **Eletroduto com ligações**: links para o ponto de origem e o de destino (os vértices seguem os
      pontos quando eles se movem), diâmetro, instalação (teto/parede/piso) e lista de condutores
      (circuito, função fase/neutro/retorno/terra, seção). As marcas NBR 5444 saem dessa lista.
- [ ] **Circuito como objeto**: número, descrição, fase, tensão, disjuntor (A, curva), seção, DR.
      Pontos e condutores apontam para ele.
- [ ] **Quadro de cargas, balanço de fases e diagrama unifilar gerados da planta** e atualizados
      quando ela muda (tabela TechDraw ou Spreadsheet).
- [ ] **Verificações**: soma de cargas por circuito e fase, disjuntor compatível com a seção,
      condutores coerentes entre trechos (um circuito não "some" no meio do caminho), pontos sem
      circuito, eletrodutos sem condutores.
- [ ] **Ferramentas MCP de consulta**: listar circuitos, pontos de um circuito, cargas por fase,
      levantamento de material (metros de eletroduto e de fio por seção, quantidade de pontos).
- [ ] **Compatibilidade**: desenhos antigos continuam abrindo como estão; um conversor recria os
      símbolos antigos como objetos novos quando possível (posição e textos do grupo).
- [ ] Comentários de revisão (`review.py`) passam a descrever o entorno pelo tipo e circuito do ponto,
      não só pelo nome do grupo.
