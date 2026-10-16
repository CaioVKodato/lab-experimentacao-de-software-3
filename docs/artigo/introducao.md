# Introdução (rascunho para o artigo SBC) — Issue #12

> Texto para colar no [template SBC no Overleaf](https://www.overleaf.com/latex/templates/sbc-conferences-template/blbxwjwzdngr).
> Escrito **antes** de inspecionar os resultados das métricas na amostra final.
>
> **Overleaf do grupo:** _preencher URL após criar o projeto_  
> **PDF:** _anexar/linkar quando disponível_

## Texto da Introdução

As métricas DORA (*DevOps Research and Assessment*) tornaram-se referência para avaliar o desempenho de entrega de software. Popularizadas por *Accelerate* (Forsgren, Humble e Kim, 2018) e atualizadas pelos relatórios *Accelerate State of DevOps*, as quatro métricas clássicas — frequência de deploy, *lead time for changes*, taxa de falha de mudança (*change failure rate*, CFR) e tempo de recuperação — capturam, em conjunto, *throughput* e estabilidade. Em 2024, o DORA acrescentou ainda a *deployment rework rate*, reforçando o interesse em medir retrabalho associado a deploys corretivos.

Medir essas métricas em repositórios *open source* exige **proxies**: o GitHub não registra “deploy em produção” nem “falha em produção”. Releases publicadas e execuções de CI/CD (GitHub Actions) são usadas como aproximações de entrega e de falha. Proxies, porém, podem distorcer conclusões: uma biblioteca que publica releases mensais não necessariamente “deploya” um serviço contínuo; falha de *pipeline* não é o mesmo que incidente em produção. Por isso, além de calcular as métricas, este trabalho (i) valida heurísticas automáticas contra julgamento humano (amostra-ouro) e (ii) avalia a sensibilidade das classificações DORA a mudanças nas definições operacionais.

O objetivo deste laboratório é minerar as métricas DORA a partir de repositórios populares que usam GitHub Actions, com definições operacionais alinhadas à turma (janela de 12 meses, *default branch*, release publicada como unidade de deploy, *workflow runs* de `push`, etc.), produzir um *pipeline* reprodutível e discutir hipóteses informais **antes** da análise dos dados.

### Hipóteses informais por questão de pesquisa

As hipóteses abaixo são expectativas do grupo, justificadas pelo referencial DORA e pelas limitações conhecidas dos proxies. A divisão por integrante segue a área de responsabilidade sugerida no enunciado (A / B / C).

**RQ 01 — Frequência de deploys** *(integrante A)*  
*H1:* Repositórios populares com CI/CD ativo apresentarão, em mediana, frequência de releases na faixa *Medium* ou *High* da tabela da disciplina (≥ 1 por mês), porém poucos alcançarão *Elite* (≥ 7 por semana), porque releases em projetos *open source* costumam ser mais esparsas do que deploys contínuos em produtos SaaS.

**RQ 02 — Lead time for changes** *(integrante B)*  
*H2:* A variante (a) (por release, a partir do commit mais antigo) terá mediana sistematicamente maior que a variante (b) (por commit). Um commit antigo “esquecido” em uma release infla (a), enquanto (b) dilui esse efeito na mediana de todos os commits.

**RQ 03 — Change failure rate** *(integrantes B e C)*  
*H3a (proxy de CI, integrante C):* A CFR (a), baseada em falhas de *workflow runs*, será maior que a CFR (b) de entrega, porque *pipelines* falham por razões alheias a defeitos em produção (flakiness, ambiente, dependências).  
*H3b (proxy de entrega, integrante B):* A CFR (b), baseada em releases corretivas em até 7 dias, situará a maior parte dos repositórios nas faixas *Elite* ou *High* (≤ 30%), sob a hipótese de que releases públicas de projetos maduros são relativamente estáveis — sujeito à qualidade da heurística de “release corretiva”.

**RQ 04 — Tempo de recuperação** *(integrante C)*  
*H4:* A mediana do tempo de recuperação (episódios de falha de CI até o próximo sucesso no mesmo *workflow*) será tipicamente da ordem de horas a poucos dias (*High*/*Medium*), com proporção não desprezível de episódios censurados (falha sem sucesso até o fim da janela), refletindo *workflows* abandonados ou pouco mantidos.

**RQ 05 — Frequência de deploy × CFR** *(integrante B)*  
*H5:* A correlação de Spearman entre frequência de deploy (RQ 01) e CFR (RQ 03) será fraca ou nula (próxima de zero), e não fortemente positiva. Isso alinharia os dados *open source* à afirmação do DORA de que velocidade e estabilidade **não** formam um *trade-off* inevitável — ainda que correlação não implique causalidade.

**RQ 06 — Características associadas a melhor desempenho** *(integrante A)*  
*H6:* Ao menos um dos fatores examinados (linguagem principal, quartis de estrelas, contribuidores, idade ou tipo de projeto) apresentará diferença estatisticamente detectável (Kruskal-Wallis / Mann-Whitney, após correção de Holm) em alguma métrica DORA, porém com tamanho de efeito pequeno a moderado. Em particular, esperamos que repositórios com mais contribuidores e tipo “aplicação/serviço” tendam a maior frequência de release do que bibliotecas puramente versionadas.

**RQ 07 — Sensibilidade às definições operacionais** *(integrante C)*  
*H7:* Uma fração relevante dos repositórios mudará de categoria DORA (Elite/High/Medium/Low) ao alternar combinações de definição (release vs. release+pré-release vs. *tag*; lead time a vs. b; CFR a vs. b). O kappa de Cohen ponderado entre pares de combinações será no máximo moderado, indicando que conclusões das RQ 01–06 devem ser lidas com cautela quanto à escolha operacional.

### Organização do restante do artigo

A Seção de Metodologia detalha fonte de dados, funil de seleção, definições e o protocolo de validação manual. Em seguida reportamos resultados por RQ, discutimos hipóteses versus achados e analisamos ameaças à validade (construto, interna, externa e de conclusão), incluindo a replicação cruzada prevista na entrega final.
