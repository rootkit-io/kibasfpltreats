# ---------------------------------------------------------------------------
# Kiba's FPL Treats -- public Projections Dashboard (Next.js 15, standalone)
# Multi-stage: deps -> build -> minimal runtime image (Phase 9 architecture).
# ---------------------------------------------------------------------------

# ----- Stage 1: install dependencies ---------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ----- Stage 2: build -------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Clerk's publishable key is a NEXT_PUBLIC_* value, so Next.js INLINES it into
# the client chunks, the prerendered HTML and the middleware bundle during
# `next build`. Supplying it only as a runtime `environment:` entry in
# docker-compose is too late -- the browser bundle would ship an undefined key.
# Passed through from docker-compose `build.args`.
#
# NOTE: only the *publishable* (public) key belongs here. CLERK_SECRET_KEY must
# stay a runtime env var -- build args are recorded in the image history.
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY

# Post-auth redirect targets. Also NEXT_PUBLIC_*, so also inlined at build
# time -- setting these only in the container's runtime env has no effect.
# Optional: app/layout.tsx defaults them to /dashboard when unset.
ARG NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL
ENV NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=$NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL
ARG NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL
ENV NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=$NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL

# Requires `output: "standalone"` in next.config.js.
RUN npm run build

# ----- Stage 3: runtime -----------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Run as a non-root user.
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# Standalone server + static assets only; no node_modules, no source.
#
# Paths are FLAT (./server.js, ./.next/static) rather than nested under
# apps/dashboard/. This app ships its own package-lock.json, so Next.js roots
# file tracing at the app directory and emits .next/standalone/server.js at the
# top level. If this repo is ever converted into a real npm-workspaces monorepo
# with a root lockfile, the tracing root moves up and these paths must become
# /app/apps/dashboard/.next/... with CMD ["node", "apps/dashboard/server.js"].
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
# public/ is NOT bundled into the standalone output; copy it explicitly or
# everything under /public (e.g. logo.svg) 404s in production.
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
