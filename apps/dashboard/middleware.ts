import { clerkMiddleware } from "@clerk/nextjs/server";

/**
 * Clerk middleware (Phase 13).
 *
 * `clerkMiddleware()` with no arguments populates `auth()` for every
 * request but does NOT automatically protect routes. Protection is
 * enforced in the server component via `(await auth()).protect()`, which
 * redirects unauthenticated users to Clerk's hosted sign-in page.
 *
 * The matcher skips Next.js internals and static assets so Clerk's Edge
 * runtime never runs on files that can't carry a session anyway.
 */
export default clerkMiddleware();

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
