Objetivo: minimizar consumo de tokens, tool calls e alterações desnecessárias.

**Fluxo de trabalho**

* Buscar antes de ler.
* Ler antes de editar.
* Expandir contexto gradualmente.
* Editar apenas após localizar o trecho exato e identificar o ponto de alteração.

**Leitura**

* Nunca leia arquivos inteiros sem necessidade.
* Localize trechos relevantes antes de abrir arquivos.
* Leia apenas o intervalo necessário.
* Expanda a leitura somente quando faltar contexto.
* Não releia arquivos, resultados ou tool calls já obtidos para o mesmo recurso na mesma sub-tarefa, salvo se o recurso tiver sido modificado.

**Comandos**

* Prefira comandos de saída curta.
* Evite listagens amplas, logs extensos, diffs completos e buscas sem filtro.
* Escolha a ferramenta mais eficiente para o ambiente disponível.
* Utilize comandos compostos apenas quando reduzirem tool calls sem ocultar erros.

**Edição**

* Faça a menor alteração possível.
* Preserve estrutura e formatação existentes.
* Evite refatorações, reorganizações e mudanças estéticas não solicitadas.
* Mantenha o diff mínimo.

**Investigação**

* Não faça varreduras amplas quando uma busca direcionada for suficiente.
* Não abra arquivos relacionados apenas por curiosidade.
* Se o ponto de alteração já estiver identificado, tente a alteração localizada antes de buscar mais contexto.

**Retries**

* Máximo de 2 tentativas para a mesma ação.
* Na terceira falha, reporte claramente o bloqueio.

**Resposta**

* Não narre etapas intermediárias durante a execução. Não explique o que acabou de acontecer entre comandos. Não justifique a próxima ação antes de tomá-la. Execute em silêncio; fale só ao concluir ou ao encontrar bloqueio real.
* Explique decisões apenas quando houver ambiguidade, risco ou impacto arquitetural relevante.
* Ao concluir, informe apenas os arquivos alterados e um resumo curto do que foi feito.
* Não modifique ou exclua testes que não passaram, resolva os erros, ou comunique quando da impossibilidade de solução.