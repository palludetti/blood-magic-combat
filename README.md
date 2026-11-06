# Blood Magic Combat System (Sistema de Combate por Magia de Sangue)

Sistema de combate por turnos em Python 3.12+ onde personagens conjuram magias consumindo a própria vida (sem atributo de mana), com persistência atômica via **Supabase (PostgreSQL)**, funções RPC transacionais com `SELECT ... FOR UPDATE`, travas de concorrência e idempotência por `request_id`.

---

## 🏛 Arquitetura

O sistema adota os princípios de Clean Architecture e separação rígida de camadas:

- **Domínio (`src/blood_magic/domain`)**:
  - Modelos de dados e validações com **Pydantic v2** (`Character`, `Combat`, `CombatParticipant`, `CombatAction`, `Spell`).
  - Funções puras para a matemática da magia de sangue (`calculate_blood_cost`, `calculate_raw_damage`, etc.), 100% testáveis sem banco.
  - Exceções de domínio fortemente tipadas (`CombatDomainError`, `ErrorCode`).
- **Aplicação (`src/blood_magic/application`)**:
  - DTOs tipados e imutáveis (`CastSpellDTO`, `CastSpellResultDTO`, `StartCombatDTO`, etc.).
  - `CombatApplicationService` orquestrando os casos de uso.
- **Infraestrutura (`src/blood_magic/infrastructure`)**:
  - Cliente oficial `supabase-py` configurado apenas com chave anônima/pública (anon key) e autenticação JWT.
  - `SupabaseCombatRepository` delegando mutações exclusivamente para funções RPC PostgreSQL atômicas.
  - Parser robusto de erros mapeando exceções do banco para códigos de erro da aplicação.
- **Banco de Dados (`migrations/`)**:
  - Migrações SQL versionadas contendo tabelas, constraints estritas, triggers de integridade, índices, RLS (Row Level Security) e RPCs `SECURITY DEFINER`.

---

## 🩸 Matemática da Magia de Sangue

Toda a matemática opera exclusivamente com inteiros e arredondamento superior sem ponto flutuante:

1. **Custo de Sangue ($C$)**:
   $$C = \max\left(1, \left\lfloor \frac{HP_{max} \times p + 99}{100} \right\rfloor\right)$$
2. **Dano Bruto ($D_{bruto}$)**:
   $$D_{bruto} = B + P + \left\lfloor \frac{C \times m}{100} \right\rfloor$$
3. **Dano Calculado ($D_{calculado}$)**:
   $$D_{calculado} = \max(0, D_{bruto} - R)$$
4. **Dano Efetivo ($D_{efetivo}$)**:
   $$D_{efetivo} = \min(HP_{alvo\_antes}, D_{calculado})$$
5. **Atualização de HP**:
   $$HP_{conjurador\_depois} = HP_{conjurador\_antes} - C$$
   $$HP_{alvo\_depois} = HP_{alvo\_antes} - D_{efetivo}$$

### Política Sem Conjuração Suicida
- Se $HP_{atual} < C \implies$ rejeitado com `INSUFFICIENT_HP`.
- Se $HP_{atual} == C \implies$ rejeitado com `LETHAL_BLOOD_COST`.
- Conjuração só é permitida se $HP_{atual} > C$.

---

## 📋 Contrato das Operações

### `fn_cast_spell` (Conjuração de Feitiço)
- **Parâmetros**:
  - `p_combat_id` (UUID)
  - `p_request_id` (UUID)
  - `p_expected_version` (bigint)
  - `p_actor_participant_id` (UUID)
  - `p_target_participant_id` (UUID)
  - `p_spell_id` (UUID)
- **Resposta (`CastSpellResultDTO`)**:
  - `action_id` (UUID)
  - `hp_cost` (int)
  - `raw_damage` (int)
  - `calculated_damage` (int)
  - `effective_damage` (int)
  - `actor_hp_before` (int)
  - `actor_hp_after` (int)
  - `target_hp_before` (int)
  - `target_hp_after` (int)
  - `turn_number` (int)
  - `resulting_version` (int)
  - `resulting_status` ("active" | "finished")
  - `next_turn_participant_id` (UUID | null)
  - `winner_participant_id` (UUID | null)
  - `is_idempotent_replay` (bool)
- **Erros Estruturados**:
  - `UNAUTHENTICATED`, `FORBIDDEN`, `COMBAT_NOT_FOUND`, `COMBAT_NOT_ACTIVE`
  - `INVALID_PARTICIPANT`, `NOT_YOUR_TURN`, `CHARACTER_DEAD`, `SPELL_NOT_FOUND`
  - `INSUFFICIENT_HP`, `LETHAL_BLOOD_COST`, `STALE_COMBAT_VERSION`, `IDEMPOTENCY_CONFLICT`

---

## 🔒 Auditoria de Concorrência e Bloqueios

Para eliminar deadlocks e condições de corrida entre requisições concorrentes, foi estabelecida uma **hierarquia estrita de bloqueios**:

| Operação | Ordem de Bloqueio (`FOR UPDATE`) | Justificativa |
|---|---|---|
| **`fn_start_combat`** | `characters(id)` (menor UUID $\to$ maior UUID) $\to$ insert `combats` $\to$ insert `combat_participants` | Ordenação lexicográfica de UUID dos personagens impede ciclo quando dois combates tentam bloquear o mesmo par simultaneamente. |
| **`fn_cast_spell`** | `combats(id)` $\to$ `combat_participants(id)` (menor UUID $\to$ maior UUID) $\to$ `characters(id)` (menor UUID $\to$ maior UUID) | O combate é o agregado raiz e o lock imediato serializa as ações do mesmo combate. A sincronização de HP nos personagens segue a ordem lexicográfica. |
| **`fn_cancel_combat`** | `combats(id)` $\to$ `combat_participants(id)` (menor UUID $\to$ maior UUID) $\to$ `characters(id)` (menor UUID $\to$ maior UUID) | Segue rigorosamente a mesma hierarquia de `fn_cast_spell`. |

**Eliminação de Ciclos com Triggers:**
Os triggers diferidos de integridade (`trg_character_active_combat_exclusivity` e `trg_combat_status_active_exclusivity`) apenas realizam leituras agregadas no final da transação sem bloquear linhas em ordem inversa.

---

## 🚀 Configuração e Execução

### 1. Pré-requisitos
- Python 3.12 ou superior
- Conta ou instância do Supabase / PostgreSQL

### 2. Instalação de Dependências
```bash
python -m pip install -r requirements.txt
```

### 3. Configuração do Supabase e Migrações
1. No painel do Supabase, acesse **SQL Editor**.
2. Execute as migrações na ordem:
   - `migrations/001_initial_schema.sql` (Estrutura, Triggers diferidos, RLS, Revogações explícitas e Índices)
   - `migrations/002_rpcs.sql` (Funções transacionais atômicas `SECURITY DEFINER` e permissões restritas a `authenticated`)
   - `migrations/003_seed_spells.sql` (Catálogo imutável de feitiços de sangue)

3. Configure o arquivo `.env`:
```bash
cp .env.example .env
```
Preencha `SUPABASE_URL`, `SUPABASE_ANON_KEY` e as credenciais de usuários autenticados para os testes e a CLI:
```ini
SUPABASE_URL=https://<your-project-id>.supabase.co
SUPABASE_ANON_KEY=eyJ...
TEST_USER_1_EMAIL=mage1@test.io
TEST_USER_1_PASSWORD=BloodPass123!Secure
TEST_USER_2_EMAIL=mage2@test.io
TEST_USER_2_PASSWORD=BloodPass456!Secure
TEST_DATABASE_URL=postgresql://postgres:<password>@<db-host>:5432/postgres
```

> ⚠️ **Atenção à Segurança:** Nunca utilize ou versione a `service_role_key`. Todo o acesso cliente utiliza a `anon_key` com JWT do usuário logado e RLS ativo.

---

## 🧪 Testes Automatizados com Pytest

Execute a suíte de testes:

```bash
python -m pytest tests -v
```

### Categorias de Testes:
1. **Regras de Negócio e Domínio Puro (`tests/test_pure_blood_math.py`)**:
   - Validação da fórmula de custo, arredondamento superior (ceiling).
   - Defesa reduzindo dano a zero sem impedir dedução integral de custo.
   - Capping de dano efetivo no HP restante do alvo.
   - Rejeição estrita de custo maior (`INSUFFICIENT_HP`) e custo igual (`LETHAL_BLOOD_COST`).
   - Bloqueio de ação e alvo mortos.
2. **Equivalência Algorítmica Python/Expressão (`tests/test_math_expression_equivalence.py`)**:
   - Comprova paridade matemática entre as funções puras Python e a lógica espelhada do SQL.
3. **Equivalência Real com Servidor PostgreSQL (`tests/test_live_postgresql_equivalence.py`)**:
   - Conecta a uma instância PostgreSQL via `TEST_DATABASE_URL` e executa as expressões reais `SELECT GREATEST(...)`, `SELECT LEAST(...)` comparando diretamente com Python.
4. **Integração Real Supabase / RLS (`tests/test_supabase_integration.py`)**:
   - Ciclo de vida completo do combate, alternância de turnos, término com vencedor, rejeição sem mutação de estado e isolamento cruzado entre dois usuários autenticados distintos.
5. **Concorrência e Rollback (`tests/test_concurrency_and_rollback.py`)**:
   - Disputa concorrente por versão esperada, replay idempotente simultâneo e bloqueio de criação concorrente de combate com o mesmo personagem.

---

## 💻 Demonstração por Terminal (CLI)

Após configurar o arquivo `.env` com as credenciais do Supabase e do usuário autenticado:

```bash
python src/blood_magic/cli.py
```
A demonstração simula a criação dos personagens, início de combate com snapshots de iniciativa, conjuração de turno com validação de sangue, dedução de HP, idempotência de repetição e bloqueio de suicídio.
