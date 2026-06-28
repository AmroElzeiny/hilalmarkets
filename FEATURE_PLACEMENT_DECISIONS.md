# Feature Placement Decisions

| Feature | Placement | Reason |
|---|---|---|
| Edge Health Score | Monitor detail and Strategy Cockpit | The detail page explains one monitor; the cockpit compares all monitors. |
| Condition Bottleneck Map | Monitor detail | Bottlenecks are meaningful in the context of one strategy version family. |
| Missed Move Analyzer | Strategy Cockpit | It is an investigation workflow, not a normal alert setting. |
| False Alert Trainer | Inbox cards plus Telegram/Discord alert actions | Feedback belongs beside the evidence being reviewed. |
| Version A/B Testing | Monitor detail | Versions share ownership and are promoted from one monitor. |
| Setup Lifecycle | Existing Lifecycles page and inbox | Lifecycles already have persistent cards and interactive chart evidence. |
| Proof Receipts | Alerts, inbox, Telegram, Discord, exports | Proof must travel with the alert and remain reconstructable. |
| Alert Frequency Forecast | Cockpit monitor cards and monitor API | It is useful before activation and during later review. |
| Smart Universe Optimizer | Monitor detail | Universe rules are part of a specific monitor. |
| Conflict Detector | Strategy Builder and publish gate | Users should see warnings while editing; critical issues must block activation. |
| Practical Improvement Buttons | Monitor detail | Suggestions need real health, feedback, and bottleneck context. |
| Personal Strategy Memory | Settings and prompt interpretation | Users can view/reset it, while the interpreter can reuse safe defaults. |
| Alert Quality Inbox | Strategy Cockpit | It is the central review center across monitors and event types. |
| Setup Replay Timeline | Inbox details and existing lifecycle chart | Timeline evidence is attached to a setup, not exposed as a disconnected raw page. |
| Strategy Decay Detector | Monitor detail, cockpit inbox, worker | Decay is periodic monitoring and should surface only when evidence exists. |

## Progressive Disclosure

- Overview: counts and direct actions.
- Strategy Cockpit: monitor health cards, missed-move form, and review inbox.
- Monitor detail: component scores, bottlenecks, version comparison, universe preview, and
  safe improvement drafts.
- Lifecycles: setup-specific state progression, condition evidence, and chart interaction.
- Raw strategy schema: hidden inside an advanced disclosure.

The previously hidden standalone replay and Near-Miss pages remain hidden. Their useful
evidence is integrated into Lifecycles and the Strategy Cockpit instead.
