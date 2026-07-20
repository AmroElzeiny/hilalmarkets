# Hilal Markets landing page

Production-ready React, TypeScript, Vite, and Tailwind CSS implementation of the supplied Hilal Markets Figma landing page.

## Run locally

Requirements: Node.js 20.19+ (or 22.12+) and pnpm 10+.

```bash
pnpm install --frozen-lockfile
pnpm dev
```

Open the local URL printed by Vite.

## Production build

```bash
pnpm typecheck
pnpm build
pnpm preview
```

The production output is written to `dist/`.

## Application integration

The production landing and contact pages are served by the repository's FastAPI
application. Their forms use the same-origin `/api/v1/public-forms` service with
server-issued CSRF protection and idempotency. Google Sheet delivery, SMTP
credentials, and recipients are server-only settings and are never bundled here.

Analytics configuration is injected into the Jinja shell at request time. The
React application calls only the provider-agnostic functions in `src/analytics.ts`;
GA4/GTM and Meta initialize only after their matching consent category is granted.

## Motion

Major headings, cards, feature rows, trust cards, FAQ content, and calls to action reveal once as they enter the viewport. Motion is automatically disabled when the visitor enables reduced-motion preferences.
