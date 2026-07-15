# Route Placeholder Map

All production links intentionally use named placeholders. Replace them with `url_for(...)`, Jinja variables, or the project's existing route helpers.

| Placeholder | Intended destination |
|---|---|
| `TODO_HOME` | Public landing page |
| `TODO_LOGIN` | Login |
| `TODO_SIGNUP` | Signup |
| `TODO_DASHBOARD_HOME` | Authenticated Home |
| `TODO_SCREENED_MARKET` | Screened Market |
| `TODO_PASSPORT` | Asset Evidence Passport |
| `TODO_WATCHLIST` | Approved Watchlist |
| `TODO_WATCH_PLANS` | Watch Plans |
| `TODO_WATCH_PLAN_BUILDER` | Guided Watch Plan Builder |
| `TODO_CHECK_MARKET` | One-time screened market check |
| `TODO_ACTIVITY` | Opportunities & Evidence |
| `TODO_OPPORTUNITY_DETAIL` | Opportunity detail |
| `TODO_COMPLIANCE` | Compliance Changes |
| `TODO_METHODOLOGY` | How We Screen |
| `TODO_INTEGRATIONS` | Integrations |
| `TODO_BILLING` | Plan & Billing |
| `TODO_SETTINGS` | Settings |
| `TODO_SUPPORT` | Support |
| `TODO_ADMIN_SYSTEM_BRAIN` | Protected internal admin route |
| `TODO_GOVERNANCE` | Public governance disclosure |
| `TODO_PRIVACY` | Privacy policy |
| `TODO_DISCLOSURES` | Risk and research disclosures |

## Static-preview convention

```html
<a
  href="#TODO_SCREENED_MARKET"
  data-route-placeholder="TODO_SCREENED_MARKET"
  data-preview-href="dashboard-market.html"
>
  Screened Market
</a>
```

`app.js` intercepts the click and opens `data-preview-href`. In production:

1. Replace `href`.
2. Remove `data-preview-href`.
3. Remove the preview click interception if no longer needed.
4. Keep `data-route-placeholder` temporarily for automated migration checks, then remove it.
