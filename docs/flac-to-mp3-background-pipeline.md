# Conversão FLAC para MP3 em background

## Objetivo

Implementar uma rotina assíncrona para:

- manter o arquivo original em `FLAC`;
- gerar uma cópia derivada em `MP3` quando o arquivo de origem estiver em `FLAC`;
- usar o `MP3` para consumo diário na VPS;
- enviar o `FLAC` para um serviço de backup/arquivamento;
- reduzir o espaço ocupado localmente sem perder o original.

## Princípio de modelagem

O `FLAC` deve ser tratado como arquivo mestre.

O `MP3` deve ser tratado como artefato derivado, nunca como substituto do original.

Isso evita perda de qualidade, permite reprocessamento futuro com outros parâmetros e separa claramente:

- preservação do acervo;
- uso operacional na VPS;
- cópias derivadas para reprodução.

## Fluxo proposto

1. Um arquivo é importado ou detectado pelo sistema.
2. O sistema identifica o formato do arquivo.
3. Se o formato não for `FLAC`, não há transcodificação.
4. Se o formato for `FLAC`, uma tarefa em background é agendada.
5. A tarefa gera um `MP3` correspondente.
6. O sistema valida se o `MP3` foi criado corretamente.
7. O `FLAC` é enviado ao destino de backup.
8. Após confirmação do backup, o sistema decide se mantém ou remove o `FLAC` local.
9. O registro do arquivo passa a apontar separadamente para:
   - original;
   - derivado;
   - status de processamento;
   - status de backup.

## Quando usar Celery

Celery é adequado para esse caso porque:

- a conversão de áudio não deve rodar dentro do request web;
- o processo pode consumir CPU e disco;
- falhas precisam de retry controlado;
- o processamento deve continuar mesmo sem interação do usuário.

O projeto já possui base para isso em `config/celery.py` e tarefas existentes em `apps/downloads/tasks.py`, então a nova rotina pode seguir o mesmo padrão de organização.

## Pipeline sugerida

Separar o processamento em etapas pequenas tende a facilitar observabilidade, retry e idempotência.

Etapas sugeridas:

1. identificar se o arquivo precisa de transcodificação;
2. converter `FLAC` para `MP3`;
3. validar o arquivo gerado;
4. persistir metadados e caminhos do derivado;
5. enviar o `FLAC` ao storage de backup;
6. confirmar upload e integridade;
7. marcar o estado final;
8. opcionalmente remover o `FLAC` local.

## Estados recomendados

Vale modelar explicitamente os estados da rotina. Exemplo:

- `pending`
- `queued`
- `converting`
- `converted`
- `conversion_failed`
- `backing_up`
- `backed_up`
- `backup_failed`
- `cleanup_pending`
- `completed`

Esses estados permitem saber:

- o que já foi processado;
- o que falhou;
- o que pode ser reexecutado;
- o que ainda depende de confirmação externa.

## Campos que fazem sentido no banco

Sem definir o schema final, os registros envolvidos devem conseguir armazenar algo como:

- formato original;
- caminho do arquivo original;
- caminho do arquivo derivado;
- status da conversão;
- status do backup;
- tamanho do original;
- tamanho do derivado;
- bitrate do derivado;
- timestamps de início/fim;
- mensagem de erro da última falha;
- hash/checksum quando fizer sentido;
- indicador de remoção local permitida ou concluída.

## Estratégia de armazenamento

### Opção mais alinhada ao objetivo de economizar espaço na VPS

- manter `MP3` localmente;
- enviar `FLAC` para backup externo;
- remover o `FLAC` local apenas após confirmação do backup.

### Opção mais conservadora

- manter `FLAC` e `MP3` localmente por um período;
- remover o `FLAC` local só depois de validações adicionais;
- usar limpeza posterior por job separado.

## Idempotência

A rotina deve ser idempotente.

Na prática:

- se o `MP3` já existir e estiver válido, não reconverter;
- se o `FLAC` já estiver confirmado no backup, não reenviar sem necessidade;
- se a etapa final já tiver sido concluída, não repetir limpeza;
- retries não devem gerar arquivos duplicados nem estados inconsistentes.

Isso é importante porque tarefas assíncronas podem ser reexecutadas por falha, timeout, retry ou duplicidade de agendamento.

## Concorrência e locking

É importante impedir duas conversões simultâneas para o mesmo arquivo.

Abordagens possíveis:

- lock lógico no banco;
- status transicional com checagem atômica;
- lock por caminho/hash do arquivo;
- fila dedicada com chave de deduplicação, se a arquitetura evoluir nessa direção.

Sem esse cuidado, podem ocorrer:

- reconversão duplicada;
- disputa por escrita no mesmo destino;
- inconsistência de status;
- remoção prematura do original.

## Filas e capacidade

Recomenda-se separar essa rotina em uma fila própria de mídia.

Motivos:

- transcodificação é trabalho mais pesado;
- evita competir com tarefas leves do sistema;
- permite controlar concorrência de forma independente.

Também vale limitar concorrência do worker dessa fila, especialmente em VPS menor, para evitar:

- pico de CPU;
- pressão em disco;
- aumento de latência no restante da aplicação.

## Retry

Retry deve ser reservado para falhas transitórias, por exemplo:

- indisponibilidade temporária do storage;
- erro de rede;
- timeout externo.

Em casos como:

- arquivo corrompido;
- formato inválido;
- falha determinística de processamento;

o ideal é marcar erro permanente e exigir intervenção ou reprocessamento explícito.

## Metadados

Na conversão, é importante preservar:

- título;
- artista;
- álbum;
- número da faixa;
- data;
- gênero;
- capa, quando aplicável.

Sem isso, o arquivo derivado pode ficar funcional, mas degradar a experiência de uso e organização.

## Nomenclatura e organização de paths

A organização deve distinguir claramente original e derivado.

Estratégias possíveis:

- mesmo basename com extensões diferentes;
- diretórios separados para `originals/` e `derived/`;
- uso de estrutura por artista/álbum em ambos;
- persistência explícita de `source_path` e `storage_path`.

O importante é evitar ambiguidade entre:

- local do original;
- local do derivado;
- local do backup.

## Validação do resultado

Antes de marcar sucesso, convém validar pelo menos:

- existência física do `MP3`;
- tamanho maior que zero;
- leitura básica do arquivo;
- duração compatível com a origem;
- metadados essenciais preservados, se essa regra for exigida.

## Remoção do FLAC local

A remoção do `FLAC` local não deve ocorrer logo após a conversão.

Pré-condições recomendadas:

- `MP3` gerado com sucesso;
- backup do `FLAC` concluído;
- confirmação de integridade ou sucesso do upload;
- estado persistido no banco;
- operação registrada em log.

Uma alternativa mais segura é deixar a remoção para uma tarefa separada de limpeza, executada depois.

## Observabilidade

Convém registrar:

- início e fim de cada etapa;
- arquivo processado;
- tempo gasto;
- erro detalhado;
- quantidade de tentativas;
- decisão de cleanup.

Isso facilita diagnóstico e reprocessamento.

## Riscos principais

- perda de metadados no derivado;
- remoção do `FLAC` antes da confirmação do backup;
- duplicidade de processamento;
- sobrecarga de CPU/disco na VPS;
- falha silenciosa na geração do `MP3`;
- inconsistência entre banco e filesystem;
- inconsistência entre filesystem local e storage remoto.

## Estratégia recomendada para a primeira versão

Para reduzir risco e complexidade, a primeira implementação pode seguir esta ordem:

1. detectar arquivos `FLAC` elegíveis;
2. gerar `MP3` em background;
3. registrar caminhos e status no banco;
4. enviar `FLAC` ao backup;
5. marcar como apto para cleanup;
6. adiar a remoção local do `FLAC` para uma segunda etapa.

Essa abordagem entrega o ganho estrutural principal sem misturar conversão, backup e deleção no mesmo passo inicial.

## Decisões em aberto para a implementação

Antes de codar, ainda será necessário definir:

- bitrate ou política de qualidade do `MP3`;
- biblioteca/ferramenta de transcodificação;
- serviço de backup de destino;
- regra de integridade/confirmação do backup;
- política de retenção local do `FLAC`;
- onde os caminhos de original e derivado serão modelados;
- se a rotina será disparada no upload, no scanner ou em job periódico.
