# Contributing

Obrigado por considerar contribuir com este projeto.

Este repositorio aceita contribuicoes de documentacao, organizacao de codigo, correcao de bugs e melhorias pequenas no fluxo.

## Antes de contribuir

- leia o README
- entenda o fluxo entre `data/inputs`, `data/outputs` e `src`
- evite commitar arquivos gerados em `data/outputs`

## Como contribuir

1. faca um fork do repositorio
2. crie uma branch para sua alteracao
3. implemente uma mudanca pequena e objetiva
4. atualize a documentacao se o comportamento mudar
5. abra um Pull Request com contexto suficiente para revisao

## Boas praticas

- mantenha nomes de arquivos e caminhos consistentes
- preserve a separacao entre codigo, entrada e saida
- nao misture refatoracao ampla com correcoes pequenas no mesmo PR
- prefira alteracoes que melhorem clareza e previsibilidade
- se adicionar uma nova opcao de CLI, documente no README

## Validacao minima

Antes de abrir PR, rode:

```powershell
python -m compileall src gerar_imagens_json.py organizar_imagens_json.py
python gerar_imagens_json.py --help
python organizar_imagens_json.py --help
```

## Tipos de contribuicao bem-vindos

- melhoria no README
- exemplos de JSON
- correcoes em tratamento de erro
- melhoria de estrutura de projeto
- melhoria de mensagens de CLI
- melhoria na organizacao dos manifests

## Discussao

Se a mudanca for grande, abra uma issue antes do PR para alinhar direcao.

## Autor e manutencao

Mantido por **Giovane Ines**.

- GitHub: https://github.com/Contagiovaneines
- LinkedIn: https://www.linkedin.com/in/giovaneines/
- Portfolio: https://giovane-portfolio.vercel.app/

