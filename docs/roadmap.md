# Roadmap

What the system does is defined by the [Proposal](AI%20Agent%20美股交易系统需求%20Proposal.md); this document only sets what comes first and what comes later, and tracks progress in "Progress" at the end. Each milestone consists of several OpenSpec changes, and each change touches only one node of the architecture diagram (`shared` excepted).

## Principles

- Pure logic and tests first, then the database, broker and models
- The trading side before the case side, because all the logic that can lose money directly is on the trading side
- Connect to Alpaca paper trading as soon as the trading-side core is done, so order and stop logic is tested against a real broker early. Live trading uses moomoo, sharing the business code with paper and swapping only the broker adapter
- Models come last: the case flow first runs end to end with stand-ins that give scripted conclusions

## Milestones

```
M0 Skeleton -> M1 Trading-side core -> M2 Trading side on paper -> M3 Case-side core
  -> M4 Case flow end to end -> M5 Models -> M6 Retrospectives and evaluation -> M7 Small-capital live
```

| Milestone | Scope | Done when |
| --- | --- | --- |
| M0 Skeleton | Layered directories, dependency checks, architecture tests | All checks run in CI |
| M1 Trading-side core | In order: trading calendar and clock; risk rules, budget reservation, pre-order recheck, fee estimation; order state machine, stop protection, reconciliation; emergency risk exit; degradation policy | Every rule has a spec and tests, with no external systems |
| M2 Trading side on paper | First settle the shared state store design; connect PostgreSQL, DBOS, Alpaca paper, moomoo quotes, Discord; local backup stops, emergency exit, close reconciliation, health monitoring | A hand-entered trade plan goes through risk, confirmation, ordering, stops and reconciliation on paper; 5 consecutive trading days of reconciliation with no discrepancies |
| M3 Case-side core | Stock pool, scheduler, Agent gateway, human confirmation rules | Same as M1 |
| M4 Case flow end to end | First settle data sources for news, consensus estimates and stock search; state store snapshots, case workflow and queue, scheduled heartbeat, event detection, case records and program log; stand-ins for PM and Critic | All three drivers can open cases, queueing follows the Proposal, plans from stand-ins can be ordered on paper |
| M5 Models | PM, sub-agents, Critic; shared context; query tools | Real models run on paper, with backup models, budgets and degradation all in effect |
| M6 Retrospectives and evaluation | Reviewer, experience library, signal evaluation, snapshot replay | Retrospectives are produced automatically; cases can be replayed to compare two prompt versions |
| M7 Small-capital live | First set the go-live bar; connect moomoo live trading, switch to live once the bar is met | Verify what paper cannot test: moomoo orders and stop orders, fees and settlement |

## Progress

There are three statuses: Not started, In progress, Done.

- When a milestone starts, set it to In progress and add a subsection below listing its changes; set it to Done when all changes are done and it meets its done criteria
- After a change is proposed, set it to In progress; after it is archived, set it to Done and update the done count in the overview

| Milestone | Status | Changes done |
| --- | --- | --- |
| M0 Skeleton | Done | — |
| M1 Trading-side core | Done | 12 / 12 |
| M2 Trading side on paper | In progress | 16 / 16 |
| M3 Case-side core | Done | 12 / 12 |
| M4 Case flow end to end | In progress | 15 / 18 |
| M5 Models | Not started | — |
| M6 Retrospectives and evaluation | Not started | — |
| M7 Small-capital live | Not started | — |

### M1 Trading-side core

Grouped in the order of the milestone table. Modifying changes alter features that are already done; "Type" names what they modify.

| Group | Change | Type | Status |
| --- | --- | --- | --- |
| Time | Trading calendar and clock | New | Done |
| Risk | Risk rules | New | Done |
| | Budget reservation | New | Done |
| | Pre-order recheck | New | Done |
| | Fee estimation | Modify: Risk rules, Budget reservation, Pre-order recheck | Done |
| Execution | Order state machine | New | Done |
| | Stop protection | New | Done |
| | Reconciliation | New | Done |
| Emergency exit | Emergency risk exit | New | Done |
| | Emergency cancel | Modify: Order state machine | Done |
| Degradation | Degradation policy | New | Done |
| | Pause opening when degraded | Modify: Risk rules, Budget reservation, Pre-order recheck | Done |

### M2 Trading side on paper

Grouped in the order of the milestone table; the order in the table is the implementation order.

| Group | Change | Type | Status |
| --- | --- | --- | --- |
| Storage | State store design | New | Done |
| | Trade records | New | Done |
| Integration | Alpaca paper | New | Done |
| | Stagger buy and stop orders | Modify: Order state machine, Stop protection | Done |
| | No re-replace while a replace is pending | Modify: Stop protection | Done |
| | moomoo quotes | New | Done |
| | Discord alerts and confirmation | New | Done |
| | Hand-entered plans | New | Done |
| | Startup and wiring | New | Done |
| | Deployment | New | Done |
| Risk | Relax risk defaults | Modify: Risk rules, Emergency risk exit | Done |
| Flows | Intraday patrol | New | Done |
| | Order flow | New | Done |
| | Emergency exit flow | New | Done |
| | Close reconciliation | New | Done |
| | Health monitor wiring | New | Done |

### M3 Case-side core

Grouped by the Proposal's modules; the order in the table is the implementation order. "Human confirmation rules" starts only after M2's "Order flow" and "Emergency exit flow" are done.

| Group | Change | Type | Status |
| --- | --- | --- | --- |
| Stock pool | Stock pool | New | Done |
| Scheduler | Case types and conclusions | New | Done |
| | Case queue | New | Done |
| | Case stages | New | Done |
| Triggers | Review dates and catalysts | New | Done |
| | Close check | New | Done |
| | Follow-up approval | New | Done |
| Agent gateway | Signal format | New | Done |
| | Task dispatch | New | Done |
| | Backup models | New | Done |
| | Call budget | New | Done |
| Human confirmation | Human confirmation rules | Modify: Discord alerts and confirmation, Emergency exit flow | Done |

### M4 Case flow end to end

Grouped by dependency; the order in the table is the implementation order: storage and data first, then the case flow, then queueing and the three triggers.

| Group | Change | Type | Status |
| --- | --- | --- | --- |
| Storage | Program log | New | Done |
| | Stock pool storage | Modify: Stock pool | Done |
| | Follow-up storage | Modify: Follow-up approval | Done |
| | Task and cost storage | Modify: Task dispatch, Call budget | Done |
| Data | News and filings | New | Done |
| | Earnings calendar and sectors | New | Done |
| | Stock search screen | New; Modify: Stock pool | Done |
| Cases | State store snapshots | New | Done |
| | Case records | New | Done |
| | Stand-ins | New | Done |
| | Case workflow | New; Modify: Case stages, Call budget, Program log, Stand-ins | Done |
| | Plans to orders | New; Modify: Order flow, Discord alerts and confirmation, Order state machine, Trade records, Intraday patrol, Hand-entered plans, Startup and wiring | Done |
| Scheduling | Queue runner | New; Modify: Order flow, Case workflow, Health monitor wiring, Startup and wiring, Program log, Deployment | Done |
| | Emergency exit aborts cases | Modify: Emergency exit flow, Case workflow, Order flow | Done |
| Triggers | Event detection | New; Modify: Case types and conclusions, Queue runner, State store snapshots, Case workflow, Stand-ins, Trade records, Program log, Startup and wiring | Done |
| | Thesis conditions and price alerts | New | Not started |
| | Close heartbeat | New | Not started |
| | Follow-up due | Modify: Scheduler | Not started |
