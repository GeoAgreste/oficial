# GeoAgreste — Backend + Analytics no Render

Arquivos principais:

- `app.py` — Flask, login, sessão administrativa, rastreamento e API do dashboard.
- `index.html` — site com o dashboard integrado ao login `admin`.
- `requirements.txt` — dependências Python.
- `render.yaml` — Blueprint do Render para Web Service + PostgreSQL.
- `.env.example` — exemplo das variáveis para desenvolvimento local.

## O que o sistema registra

- visitantes online agora;
- visitantes únicos hoje, últimos 7 dias e últimos 30 dias;
- visualizações de páginas;
- páginas mais acessadas;
- dispositivos (desktop, celular, tablet);
- origem aproximada (direto, busca, redes sociais ou referência);
- gráfico dos últimos 30 dias.

O visitante recebe um identificador aleatório em cookie. O sistema não precisa armazenar nome, e-mail ou IP para essas métricas.

## Render

1. Coloque `app.py`, `index.html`, `requirements.txt` e `render.yaml` na raiz do repositório.
2. Mantenha também as imagens/pastas que o HTML já utiliza.
3. No Render, crie um **Blueprint** apontando para o repositório.
4. O `render.yaml` cria o Web Service e o PostgreSQL e injeta `DATABASE_URL` automaticamente.
5. Informe uma senha forte para `ADMIN_PASSWORD` quando o Render solicitar.
6. O `SECRET_KEY` é gerado pelo próprio Render.
7. O serviço inicia com `gunicorn app:app`.

### Login

- Usuário: `admin` (configurado por `ADMIN_USER`)
- Senha: a definida em `ADMIN_PASSWORD` no Render.

A senha não fica escrita no HTML/JavaScript.

## Desenvolvimento local

Defina as variáveis de `.env.example` no ambiente e use:

```bash
pip install -r requirements.txt
python app.py
```

Para desenvolvimento HTTP local, use `COOKIE_SECURE=false`.
