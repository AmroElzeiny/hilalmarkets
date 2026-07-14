# Production Route Map

The static prototype used named placeholders. Production templates have replaced them with the following real routes.

| Prototype placeholder | Production destination |
|---|---|
| `TODO_HOME` | `/` |
| `TODO_LOGIN` | `/signin` |
| `TODO_SIGNUP` | `/signup` |
| `TODO_DASHBOARD_HOME` | `/dashboard` |
| `TODO_SCREENED_MARKET` | `/dashboard/market` |
| `TODO_PASSPORT` | `/dashboard/market/{asset_slug}` |
| `TODO_WATCHLIST` | `/dashboard/watchlist` |
| `TODO_WATCH_PLANS` | `/dashboard/strategies` |
| `TODO_WATCH_PLAN_BUILDER` | `/dashboard/strategies/new` |
| `TODO_CHECK_MARKET` | Retired prototype alias; production redirects to `/dashboard/strategies/new?mode=scanner` |
| `TODO_ACTIVITY` | `/dashboard/activity` |
| `TODO_OPPORTUNITY_DETAIL` | `/dashboard/activity/{lifecycle_id}` |
| `TODO_COMPLIANCE` | `/dashboard/compliance` |
| `TODO_METHODOLOGY` | `/dashboard/methodology` |
| `TODO_INTEGRATIONS` | `/dashboard/integrations` |
| `TODO_BILLING` | `/dashboard/billing` |
| `TODO_SETTINGS` | `/dashboard/settings` |
| `TODO_SUPPORT` | `/dashboard/support` |
| `TODO_ADMIN_SYSTEM_BRAIN` | `/system-brain` |
| `TODO_GOVERNANCE` | `/governance` |
| `TODO_PRIVACY` | `/privacy` |
| `TODO_DISCLOSURES` | `/risk-disclosure` |

## Static reference convention

```html
<a
  href="#TODO_SCREENED_MARKET"
  data-route-placeholder="TODO_SCREENED_MARKET"
  data-preview-href="dashboard-market.html"
>
  Screened Market
</a>
```

`app.js` intercepts the click only in the standalone static reference. In production:

1. Routes are real FastAPI/Jinja links.
2. Preview attributes are absent.
3. Data is supplied by authenticated backend services.
4. Legacy aliases redirect to the consolidated owner page instead of rendering duplicates.
