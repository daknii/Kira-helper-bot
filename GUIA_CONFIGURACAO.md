# Guia de Configuração — Kira Helper Bot

Passo a passo completo para configurar e operar o bot no servidor.

---

## 1. Subir ou reiniciar o bot

Sempre que houver atualização de comandos ou do banco, reinicie o bot para executar migrações automáticas e sincronizar os Slash Commands:

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

### Pré-requisitos no Discord
- **Discord Developer Portal → Bot → Privileged Gateway Intents:** ative **SERVER MEMBERS INTENT** e **MESSAGE CONTENT INTENT**.
- **Hierarquia de Cargos no servidor:** arraste o cargo do bot para **acima** dos cargos dos membros comuns para que `/timeout`, `/kick`, `/ban` e a criação de tickets funcionem corretamente.

---

## 2. Configurar canais e cargos da staff (`/configurar`)

Em qualquer canal (restrito a **Administradores**), execute `/configurar`:

| Parâmetro | Tipo | Para que serve |
| :--- | :--- | :--- |
| `canal_logs` | Canal de texto | Logs detalhados de moderação e transcripts `.txt` de tickets (`#logs`) |
| `categoria_tickets` | Categoria | Categoria onde os canais privados `ticket-usuario` são criados |
| `cargo_staff_1` | Cargo | Cargo principal da staff (ex: alto-escalão) |
| `cargo_staff_2` | Cargo | Segundo cargo de staff (opcional) |
| `cargo_staff_3` | Cargo | Terceiro cargo de staff (opcional) |
| `canal_entrada` | Canal de texto | Mensagens automáticas de boas-vindas (`#🌊・entrada`) |
| `canal_saida` | Canal de texto | Avisos discretos de saída (`#🌊・saída`) |
| `canal_punicoes` | Canal de texto | Mural público de punições (`#⚠️・punições`) |
| `canal_analise_divisao` | Canal de texto | Recebimento de pedidos de divisão (`#👑・alto-escalão`) |

- Todos os campos são **opcionais**: você pode preencher tudo de uma vez ou atualizar apenas um campo sem perder os demais.
- Rodar `/configurar` **sem parâmetros** exibe o painel atual de toda a configuração salva.

### Gerenciar múltiplos cargos de staff depois
- `/add-staff cargo:@Cargo` — adiciona um cargo à lista `staff_role_ids` (sem limite de 3 cargos).
- `/remove-staff cargo:@Cargo` — remove um cargo da lista `staff_role_ids`.

Todos os cargos da lista recebem automaticamente:
- Acesso de leitura/escrita e menção em todos os novos tickets.
- Permissão para usar todos os comandos de moderação.

---

## 3. Publicar os painéis fixos

1. **Canal de Regras:**
   ```text
   /setup-regras canal_tickets:#seu-canal-de-tickets
   ```
   Publica o embed oficial com as 11 regras do servidor já mencionando o canal de tickets na regra 11.

2. **Canal de Tickets:**
   ```text
   /setup-tickets
   ```
   Publica o painel de suporte com o botão persistente **abrir ticket** (continua ativo mesmo após reiniciar o bot).

3. **Embeds personalizados (opcional):**
   ```text
   /embed
   ```
   Abre um formulário para enviar comunicados com título, descrição, cor hex opcional (padrão `#2B2D31`) e URL de imagem opcional.

---

## 4. Comandos de Moderação

Liberados para **Administradores**, membros com a **permissão nativa** correspondente ou qualquer membro que possua **um dos cargos de staff** configurados:

- `/warn <membro> <motivo>` — registra advertência no SQLite, envia DM (tratando DM fechada), publica no chat, no mural de punições e nos logs.
- `/warns <membro>` — lista todas as advertências do membro (ID, motivo, moderador e data).
- `/unwarn <id_warn>` — remove uma advertência pelo ID e registra nos logs.
- `/timeout <membro> <minutos> [motivo]` — aplica castigo nativo (1 a 40320 min) e registra nos logs/punições.
- `/kick <membro> [motivo]` — expulsa respeitando hierarquia de cargos e registra nos logs/punições.
- `/ban <membro> [motivo] [deletar_mensagens_dias]` — bane respeitando hierarquia e registra nos logs/punições.
- `/clear <quantidade>` — apaga de 1 a 100 mensagens do canal atual com resposta privada e log.

---

## 5. Checklist rápido de validação

- [ ] `/configurar` executado e todos os canais/cargos exibidos corretamente.
- [ ] `/setup-regras` enviado no canal de regras.
- [ ] `/setup-tickets` enviado no canal de suporte.
- [ ] Teste de **abrir ticket** → canal privado criado na categoria com todos os cargos staff mencionados.
- [ ] Teste de **fechar ticket** → transcript `.txt` enviado no `canal_logs` e canal deletado após 5s.
- [ ] Teste de `/clear quantidade:1` e `/warn` → logs no `canal_logs` e resumo no `canal_punicoes`.
